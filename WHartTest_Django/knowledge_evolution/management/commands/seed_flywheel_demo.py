"""为「反馈与测评」人机协作工作台填充一批演示数据（幂等、可重置）。

用法：
    python manage.py seed_flywheel_demo --project-id 1
    python manage.py seed_flywheel_demo --project-id 1 --reset      # 先清掉上次演示数据
    python manage.py seed_flywheel_demo --project-id 1 --dry-run    # 只统计不写库

设计要点：
1. 全部使用稳定自然键 + get_or_create / update_or_create，可重复执行不产生重复行。
2. 运行时间戳显式回填，保证「评测运行」列表顺序稳定，且最新一次运行带有完整结果
   ——否则工作台默认选中最新运行时会显示空数据。
3. 失败样本判据与 `EvaluationReviewBridge.find_failures` 完全一致：
   status='completed' 且某一层级得分低于阈值。因此界面「失败样本数」
   等于点「生成知识候选」实际能产出的候选数。
4. 所有演示数据都带可识别标记（DEMO_* 常量），--reset 按标记精确清理，
   不会误删真实数据。
"""

import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from knowledge_evolution.eval_review_bridge import EvaluationReviewBridge
from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.knowledge_models import (
    KnowledgeAsset,
    KnowledgeCandidate,
    KnowledgeEvidence,
    KnowledgeVersion,
    SourceSnapshot,
)
from knowledge_evolution.models import (
    EvaluationCase,
    EvaluationSuite,
    FeedbackEvent,
    GenerationOutput,
    RetrievalTrace,
)
from knowledge_evolution.retrieval_models import RetrievalPolicy
from projects.models import Project

# ---------------------------------------------------------------- 演示数据标记
DEMO_TAG = "seed_flywheel_demo"
DEMO_TRACE_PREFIX = "demo-trace-"
DEMO_FEEDBACK_PREFIX = "demo-fb-"
DEMO_RUN_PREFIX = "演示运行"
DEMO_CASE_PREFIX = "demo:"
DEMO_SNAPSHOT_PREFIX = "demo-"
DEMO_ASSET_PREFIX = "demo/"
DEMO_POLICY_NAME = "默认生产检索策略"

# 与 EvaluationReviewBridge.DEFAULT_THRESHOLDS 对齐，供界面展示一致性校验
FAILURE_THRESHOLDS = {"l0": 0.5, "l1": 0.5, "l2": 0.5, "l3": 0.5}

SPLIT_RATIO = {"gold": 0.2, "regression": 0.5, "fresh": 0.2, "challenge": 0.1}

# ------------------------------------------------------------------ 评测集规划
# size: 需要的样本总数；only_if_empty=True 表示已有样本时完全不补（保护既有基线集）
SUITE_PLAN = [
    {
        "name": "Phase0 种子集",
        "suite_type": "seed",
        "task_type": "code_review",
        "size": 50,
        "only_if_empty": True,
        "description": "Phase 0 可观测基线：从真实任务与模板补齐生成，用于冻结上线前的能力基线",
    },
    {
        "name": "回归基准集 v1.2",
        "suite_type": "regression",
        "task_type": "code_review",
        "size": 24,
        "only_if_empty": False,
        "description": "每次策略/模型变更后必跑，覆盖历史缺陷与高频回归路径",
    },
    {
        "name": "挑战样本集 · 边界与对抗",
        "suite_type": "challenge",
        "task_type": "knowledge_query",
        "size": 12,
        "only_if_empty": False,
        "description": "专挑易错边界：近似条款、时效冲突、跨库同义词、诱导性提问",
    },
    {
        "name": "新样本流 · 用例生成",
        "suite_type": "fresh",
        "task_type": "testcase_generation",
        "size": 8,
        "only_if_empty": False,
        "description": "近两周新入需求，用于探测能力漂移",
    },
]

# 各任务类型的样本模板：(输入, 期望/评分要点)
CODE_REVIEW_TEMPLATES = [
    ("审查 MR !{n} 中的空指针与边界条件处理", "识别出 NPE 风险点并给出防御性写法"),
    ("审查 MR !{n} 的事务边界与部分失败回滚", "指出跨服务写操作缺少补偿逻辑"),
    ("审查 MR !{n} 的并发安全与共享状态", "识别非线程安全的可变共享对象"),
    ("审查 MR !{n} 的异常吞噬与错误码映射", "指出 except 后仅 pass 的静默失败"),
    ("审查 MR !{n} 的资源释放与连接泄漏", "指出未使用 with/close 的连接持有"),
    ("审查 MR !{n} 的日志脱敏与敏感字段", "指出手机号/账号明文落盘"),
    ("审查 MR !{n} 的幂等键设计与重复提交", "指出重试路径缺少幂等保护"),
    ("审查 MR !{n} 的分页查询与深分页性能", "指出 offset 过大导致的全表扫描"),
]

KNOWLEDGE_QUERY_TEMPLATES = [
    ("如何配置测试环境的域名解析？", ["测试环境部署手册#域名解析"]),
    ("e 投票系统的并发上限是多少？", ["容量基线#e投票并发"]),
    ("债券交易撮合的异常码 0x21 代表什么？", ["接口手册#异常码", "撮合模块说明"]),
    ("综业平台的日终清算在哪一步触发？", ["清算流程#日终"]),
    ("MDTS 的行情快照频率是多少？", ["MDTS 接口说明#快照频率"]),
    ("竞价阶段涨跌停校验的边界规则是什么？", ["竞价业务规则#涨跌停"]),
    ("测试环境 Redis 集群的分片策略是什么？", ["基础设施#Redis 集群"]),
    ("需求变更后回归范围如何界定？", ["测试管理办法#回归范围"]),
    ("LDDS 回放数据的保留周期是多久？", ["LDDS 说明#数据保留"]),
    ("缺陷定级为严重的判定标准有哪些？", ["缺陷管理规范#定级"]),
]

TESTCASE_GENERATION_TEMPLATES = [
    ("为竞价撮合超时场景生成测试用例", "覆盖正常超时、重试成功、重试耗尽三类分支"),
    ("为 LDDS 行情回放生成断点续传用例", "覆盖断点丢失、重复回放、乱序到达"),
    ("为 e 投票重复提交生成幂等用例", "覆盖同用户并发、跨端并发、重放攻击"),
    ("为债券异常码 0x21 生成异常路径用例", "覆盖可重试与不可重试两类异常码"),
]

# --------------------------------------------------------------- 检索轨迹规划
# (task_type, query, status, token_usage, channels, error_code)
TRACE_SEEDS = [
    ("knowledge_query", "如何配置测试环境的域名解析？", "completed", 1830,
     {"dense": 12, "sparse": 8, "structured": 4, "graph": 3}, ""),
    ("knowledge_query", "e 投票系统的并发上限是多少？", "completed", 1240,
     {"dense": 9, "sparse": 5, "structured": 3, "graph": 2}, ""),
    ("knowledge_query", "债券交易撮合的异常码 0x21 代表什么？", "completed", 2260,
     {"dense": 14, "sparse": 11, "structured": 5, "graph": 4}, ""),
    ("knowledge_query", "综业平台的日终清算在哪一步触发？", "completed", 1580,
     {"dense": 10, "sparse": 6, "structured": 4, "graph": 3}, ""),
    ("code_review", "审查 MR !1287 的空指针风险", "completed", 4120,
     {"dense": 18, "sparse": 7, "structured": 6, "graph": 9}, ""),
    ("code_review", "审查 MR !1301 的事务边界", "completed", 3890,
     {"dense": 16, "sparse": 6, "structured": 5, "graph": 8}, ""),
    ("code_review", "审查 MR !1315 的并发安全", "failed", 640,
     {"dense": 4}, "UPSTREAM_TIMEOUT"),
    ("testcase_generation", "为竞价撮合超时场景生成用例", "completed", 2760,
     {"dense": 11, "sparse": 4, "structured": 7, "graph": 5}, ""),
    ("testcase_generation", "为 LDDS 行情回放生成用例", "completed", 2410,
     {"dense": 9, "sparse": 4, "structured": 6, "graph": 4}, ""),
    ("knowledge_query", "MDTS 的行情快照频率是多少？", "completed", 1120,
     {"dense": 8, "sparse": 4, "structured": 3, "graph": 2}, ""),
    ("knowledge_query", "竞价阶段涨跌停校验规则", "untraceable", 940,
     {"dense": 6, "sparse": 3}, "NO_CITATION"),
    ("test_execution", "执行 G4 版本回归套件 3", "completed", 3300,
     {"dense": 7, "sparse": 3, "structured": 9, "graph": 3}, ""),
    ("code_review", "审查 MR !1330 的日志脱敏", "completed", 3560,
     {"dense": 15, "sparse": 5, "structured": 4, "graph": 7}, ""),
    ("knowledge_query", "测试环境 Redis 集群的分片策略是什么？", "completed", 1470,
     {"dense": 9, "sparse": 5, "structured": 3, "graph": 2}, ""),
]

# --------------------------------------------------------------- 反馈事件规划
# (signal, trace_index, reason_code, comment, actor_type, value)
FEEDBACK_SEEDS = [
    ("accepted", 0, "accepted_answer", "答案直接可用，已同步到值班手册", "user", 1.0),
    ("accepted", 3, "accepted_answer", "日终触发点定位准确", "user", 1.0),
    ("rejected", 1, "wrong_number", "并发上限是旧口径，新版本已调整为 8000", "user", -1.0),
    ("rejected", 9, "stale_knowledge", "MDTS 快照频率已从 3s 调整为 1s", "user", -1.0),
    ("edited", 2, "edit_content", "补充了 0x21 的可重试判定条件", "user", 0.5),
    ("edited", 4, "edit_content", "把 NPE 风险点按严重程度重新排序", "user", 0.5),
    ("test_passed", 5, "ci_passed", "事务边界修复后回归套件全绿", "integration", 1.0),
    ("test_failed", 6, "ci_failed", "并发用例在第 3 轮压测出现数据竞争", "integration", -1.0),
    ("defect_confirmed", 6, "defect_created", "已提单 DEF-2026-1188，定级严重", "user", -1.0),
    ("false_positive", 11, "risk_false_alarm", "标记为高风险的 2 处为框架层已保证，属误报", "user", -1.0),
    ("missed", 12, "missed_risk", "漏报了日志里的账号明文，人工复查才发现", "user", -1.0),
    ("accepted", 7, "accepted_answer", "超时分支覆盖完整，直接纳管", "user", 1.0),
    ("test_passed", 7, "ci_passed", "生成的用例在预发环境全部通过", "integration", 1.0),
    ("merged", 8, "merged_duplicate", "与既有用例合并去重后入库", "system", 0.5),
    ("reverted", 10, "reverted_publish", "该条规则上线后误伤正常流量，已回退", "system", -1.0),
    ("accepted", 13, "accepted_answer", "Redis 分片策略与运维记录一致", "user", 1.0),
]

# ----------------------------------------------------------------- 知识候选规划
# (kind, origin, level, confidence, state, payload, review_reason)
CANDIDATE_SEEDS = [
    ("rule", "evaluation_failure", "L2", 0.42, "awaiting_approval",
     {"title": "连字符域名解析必须走平台通道而非宿主机 hosts",
      "statement": "容器不继承宿主机 /etc/hosts，测试域名需通过 compose extra_hosts 或平台通道下发。",
      "scope": "测试环境部署"},
     "评测 run 中 l2 得分 0.38，低于阈值 0.50：域名解析类问答连续 3 次给出宿主机改法"),
    ("rule", "evaluation_failure", "L2", 0.46, "awaiting_approval",
     {"title": "跨服务写操作必须带补偿或本地消息表",
      "statement": "涉及两个以上服务写库的路径，缺少补偿逻辑时部分失败会导致数据不一致。",
      "scope": "代码审查"},
     "评测 run 中 l2 得分 0.41，低于阈值 0.50：事务边界类 MR 漏报率偏高"),
    ("rule", "evaluation_failure", "L2", 0.38, "awaiting_approval",
     {"title": "异常码需区分可重试与不可重试",
      "statement": "0x21 属业务拒绝不可重试，0x30 系列为瞬时故障可重试，二者不可混用重试策略。",
      "scope": "债券交易撮合"},
     "评测 run 中 l3 得分 0.29，低于阈值 0.50：异常码语义混淆导致重试风暴"),
    ("concept", "distillation", "L2", 0.78, "pending",
     {"title": "检索轨迹（RetrievalTrace）",
      "statement": "一次检索请求的完整可观测记录：召回通道、候选、引用、耗时与 token 开销。",
      "scope": "知识飞轮"},
     "从 12 篇内部文档二次蒸馏出的核心概念，待与既有词条合并"),
    ("concept", "distillation", "L2", 0.74, "pending",
     {"title": "影子门禁（Shadow Gate）",
      "statement": "新版本先在影子流量上跑，指标不劣于基线才允许晋级，异常自动回滚。",
      "scope": "知识飞轮"},
     "蒸馏自设计文档 §12，待补充指标口径后入库"),
    ("experience", "extraction", "L3", 0.83, "pending",
     {"title": "黑屏类问题的排查顺序：接口先于渲染",
      "statement": "移动端黑屏先查接口返回与抓包，再查渲染层，可避免把网络问题误判为前端缺陷。",
      "scope": "移动端调试"},
     "从 6 条历史缺陷单中抽取的共性经验"),
    ("knowledge_atom", "evaluation_failure", "L2", 0.51, "evaluating",
     {"title": "e 投票并发容量基线",
      "statement": "单集群并发上限 8000，超过后需先扩容再压测。",
      "scope": "容量基线"},
     "评测 run 中 l1 得分 0.47，低于阈值 0.50：旧口径 5000 已被新版本推翻"),
    ("rule", "extraction", "L2", 0.88, "accepted",
     {"title": "测试环境域名一律通过平台通道下发",
      "statement": "禁止在宿主机手工维护 hosts，统一由平台通道同步到各执行器容器。",
      "scope": "测试环境部署"},
     "人工审核通过：与既定部署纪律一致，已关联资产"),
    ("fact", "extraction", "L3", 0.91, "accepted",
     {"title": "LDDS 回放数据保留 30 天",
      "statement": "回放原始数据保留 30 天，超期归档至冷存储，恢复需提前一天申请。",
      "scope": "LDDS"},
     "人工审核通过：与运维记录核对一致"),
    ("rule", "manual", "L2", 0.62, "rejected",
     {"title": "代码审查必须逐行给出修改建议",
      "statement": "要求所有审查意见都附带可粘贴的补丁片段。",
      "scope": "代码审查"},
     "人工驳回：粒度要求过强，与分级审查策略冲突"),
    ("relation", "distillation", "L2", 0.55, "conflicted",
     {"title": "MDTS 快照频率与行情回放周期的耦合关系",
      "statement": "快照频率 1s，回放周期需为整数倍，否则出现时间轴错位。",
      "scope": "MDTS / LDDS"},
     "检测到与既有条目冲突：旧值为 3s 倍数，待人工裁决适用域"),
    ("summary", "distillation", "L2", 0.69, "merged",
     {"title": "G4 版本回归范围界定摘要",
      "statement": "竞价、综业、债券、LDDS、MDTS 五个域的变更均需全量回归，e 投票按影响面裁剪。",
      "scope": "测试管理"},
     "与既有关联条目合并入库"),
]


def stable_ratio(*parts) -> float:
    """由稳定输入派生 [0,1) 的伪随机数，保证重复执行结果一致。"""
    text = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / float(0x100000000)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def case_split(index: int) -> str:
    """按 2:5:2:1 把第 index 个样本映射到 split（与 build_seed_evaluation_set 一致）。"""
    bucket = index % 10
    if bucket < 2:
        return "gold"
    if bucket < 7:
        return "regression"
    if bucket < 9:
        return "fresh"
    return "challenge"


class _DryRunRollback(Exception):
    """dry-run 时用于回滚事务，保证不写库。"""


class Command(BaseCommand):
    help = "为反馈与测评工作台填充演示数据（幂等）"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=str, required=True, help="项目 ID")
        parser.add_argument("--reset", action="store_true", help="先清理上次生成的演示数据")
        parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写库")
        parser.add_argument("--actor", type=str, default="", help="操作人用户名，默认取超级管理员")

    # ------------------------------------------------------------------ 入口
    def handle(self, *args, **options):
        project_id = options["project_id"]
        try:
            project = Project.objects.get(pk=project_id)
        except (Project.DoesNotExist, ValueError):
            raise CommandError(f"项目 {project_id} 不存在")

        actor = self._resolve_actor(options["actor"])
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.NOTICE(
                f"[dry-run] project={project.pk} actor={actor} —— 结束时会回滚，不写库"
            ))

        try:
            with transaction.atomic():
                if options["reset"]:
                    self._reset(project)
                snapshot = self._seed_assets(project, actor)
                suites = self._seed_suites(project, actor)
                policy = self._seed_policy(project, actor)
                traces, outputs = self._seed_traces(project, actor, policy)
                self._seed_feedback(project, actor, traces, outputs)
                runs = self._seed_runs(project, actor, suites["Phase0 种子集"], policy)
                self._seed_candidates(project, actor, snapshot, runs)
                if dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            self.stdout.write(self.style.WARNING("已回滚（dry-run）"))
            return

        if options["reset"]:
            self.stdout.write(self.style.WARNING("已清理上次的演示数据"))
        self._report(project)

    # ------------------------------------------------------------------ 子步骤
    def _resolve_actor(self, username):
        user_model = get_user_model()
        if username:
            actor = user_model.objects.filter(username=username).first()
            if not actor:
                raise CommandError(f"用户 {username} 不存在")
            return actor
        return (
            user_model.objects.filter(is_superuser=True).order_by("id").first()
            or user_model.objects.order_by("id").first()
        )

    def _reset(self, project):
        """按演示标记精确清理，不触碰真实数据。"""
        trace_qs = RetrievalTrace.objects.filter(
            project=project, task_id__startswith=DEMO_TRACE_PREFIX
        )
        output_qs = GenerationOutput.objects.filter(
            project=project, task_id__startswith=DEMO_TRACE_PREFIX
        )
        run_qs = EvaluationRun.objects.filter(
            suite__project=project, name__startswith=DEMO_RUN_PREFIX
        )

        # 顺序：先删依赖方，再删被依赖方（FeedbackEvent 对 output/trace 是 PROTECT）
        FeedbackEvent.objects.filter(
            project=project, idempotency_key__startswith=DEMO_FEEDBACK_PREFIX
        ).delete()
        run_qs.delete()
        EvaluationCase.objects.filter(
            suite__project=project, metadata__seed_key__startswith=DEMO_CASE_PREFIX
        ).delete()
        EvaluationSuite.objects.filter(
            project=project,
            name__in=[p["name"] for p in SUITE_PLAN if p["name"] != "Phase0 种子集"],
        ).delete()
        KnowledgeCandidate.objects.filter(project=project, extracted_by=DEMO_TAG).delete()
        KnowledgeEvidence.objects.filter(
            version__asset__project=project, version__asset__key__startswith=DEMO_ASSET_PREFIX
        ).delete()
        version_qs = KnowledgeVersion.objects.filter(
            asset__project=project, asset__key__startswith=DEMO_ASSET_PREFIX
        )
        KnowledgeAsset.objects.filter(
            project=project, key__startswith=DEMO_ASSET_PREFIX
        ).update(current_version=None)
        version_qs.delete()
        KnowledgeAsset.objects.filter(project=project, key__startswith=DEMO_ASSET_PREFIX).delete()
        SourceSnapshot.objects.filter(
            project=project, source_id__startswith=DEMO_SNAPSHOT_PREFIX
        ).delete()
        RetrievalPolicy.objects.filter(
            project=project, name=DEMO_POLICY_NAME, created_by__isnull=True
        ).delete()
        output_qs.delete()
        trace_qs.delete()

    def _seed_assets(self, project, actor):
        """演示用来源快照 + 2 个知识资产（含已发布版本 + 证据）。"""
        snapshot, _ = SourceSnapshot.objects.get_or_create(
            project=project,
            source_type="document",
            source_id=f"{DEMO_SNAPSHOT_PREFIX}req-2026-0912",
            defaults={
                "revision": "v1",
                "content_hash": sha256_text("测试环境部署与知识治理要求 2026-09-12"),
                "authority": "internal",
                "status": "parsed",
                "parser_version": "doc-parser@1.4",
                "parsed_text": (
                    "一、测试环境域名一律通过平台通道下发，禁止在宿主机手工维护 hosts。\n"
                    "二、容器不继承宿主机 /etc/hosts，需由平台通道或 compose 显式注入。\n"
                    "三、e 投票系统单集群并发上限 8000，压测前须确认扩容完成。\n"
                    "四、LDDS 回放原始数据保留 30 天，超期归档冷存储。\n"
                ),
                "captured_by": actor,
            },
        )

        asset_specs = [
            {
                "key": f"{DEMO_ASSET_PREFIX}rule/test-env-dns",
                "title": "测试环境域名解析配置规范",
                "asset_type": "rule",
                "level": "L2",
                "content": (
                    "测试环境域名必须通过平台通道统一下发到各执行器容器。\n"
                    "容器不继承宿主机 /etc/hosts；宿主机改了 hosts 而容器仍解析失败属于预期行为。\n"
                    "批量新增域名时，应更新 compose 的 extra_hosts 或平台通道名单，二者不要并存。"
                ),
                "change_reason": "沉淀测试环境域名解析踩坑",
                "section": "二、容器 hosts 说明",
                "excerpt": "容器不继承宿主机 /etc/hosts，需由平台通道或 compose 显式注入。",
            },
            {
                "key": f"{DEMO_ASSET_PREFIX}fact/e-voting-capacity",
                "title": "e 投票系统并发容量基线",
                "asset_type": "fact",
                "level": "L3",
                "content": (
                    "单集群并发上限 8000（旧口径 5000 已作废）。\n"
                    "超过上限时表现为请求排队而非报错，需结合网关排队指标判断。\n"
                    "压测前必须确认扩容已完成，否则结果不可用于容量结论。"
                ),
                "change_reason": "容量基线口径更新",
                "section": "三、e 投票并发上限",
                "excerpt": "e 投票系统单集群并发上限 8000，压测前须确认扩容完成。",
            },
        ]

        first_asset = None
        for spec in asset_specs:
            asset, _ = KnowledgeAsset.objects.get_or_create(
                project=project,
                asset_type=spec["asset_type"],
                key=spec["key"],
                defaults={
                    "title": spec["title"],
                    "level": spec["level"],
                    "status": "active",
                    "applicability": {"env": ["test", "staging"], "domain": "测试环境"},
                    "owner": actor,
                    "created_by": actor,
                    "updated_by": actor,
                },
            )
            version, _ = KnowledgeVersion.objects.get_or_create(
                asset=asset,
                version=1,
                defaults={
                    "content": spec["content"],
                    "content_hash": sha256_text(spec["content"]),
                    "source_snapshot": snapshot,
                    "status": "published",
                    "valid_from": timezone.now() - timedelta(days=9),
                    "approved_by": actor,
                    "approved_at": timezone.now() - timedelta(days=8),
                    "change_reason": spec["change_reason"],
                    "created_by": actor,
                },
            )
            if asset.current_version_id != version.id:
                asset.current_version = version
                asset.save(update_fields=["current_version", "updated_at"])
            # KnowledgeEvidence.save() 会用 location 重新派生 location_hash，
            # 因此幂等键只能用 version+snapshot+relation，自己算的哈希会被覆盖。
            KnowledgeEvidence.objects.update_or_create(
                version=version,
                snapshot=snapshot,
                relation="supported_by",
                defaults={
                    "location": {"section": spec["section"], "asset_key": spec["key"]},
                    "excerpt": spec["excerpt"],
                    "weight": 0.9,
                    "extractor_version": "rule-extractor@1.1",
                },
            )
            first_asset = first_asset or asset
        return snapshot

    def _seed_suites(self, project, actor):
        suites = {}
        for plan in SUITE_PLAN:
            suite, _ = EvaluationSuite.objects.get_or_create(
                project=project,
                name=plan["name"],
                suite_type=plan["suite_type"],
                defaults={
                    "task_type": plan["task_type"],
                    "description": plan["description"],
                    "split_ratio": SPLIT_RATIO,
                    "is_active": True,
                    "created_by": actor,
                },
            )
            suites[plan["name"]] = suite
            if not plan["size"]:
                continue
            if plan["only_if_empty"] and EvaluationCase.objects.filter(suite=suite).exists():
                # 已有基线样本（如 build_seed_evaluation_set 生成的真实种子集）时不补，
                # 避免污染既有基线；但全新项目上必须自给自足，否则工作台是空的。
                continue
            self._seed_cases(suite, plan)
        return suites

    def _seed_cases(self, suite, plan):
        """为演示评测集补齐样本（幂等：metadata.seed_key）。"""
        task_type = plan["task_type"]
        existing_keys = set(
            EvaluationCase.objects.filter(suite=suite).values_list(
                "metadata__seed_key", flat=True
            )
        )
        start_number = EvaluationCase.objects.filter(suite=suite).count()
        created = 0
        for index in range(plan["size"]):
            seed_key = f"{DEMO_CASE_PREFIX}{suite.suite_type}:{index}"
            if seed_key in existing_keys:
                continue
            if task_type == "code_review":
                raw, expected_hint = CODE_REVIEW_TEMPLATES[index % len(CODE_REVIEW_TEMPLATES)]
                input_payload = {"diff_ref": raw.format(n=1200 + index), "repo": "autoTestPlatformSSE"}
            elif task_type == "knowledge_query":
                raw, cites = KNOWLEDGE_QUERY_TEMPLATES[index % len(KNOWLEDGE_QUERY_TEMPLATES)]
                input_payload = {"query": raw, "expected_doc_ids": cites}
                expected_hint = "命中上述文档并给出可核对的原文出处"
            else:
                raw, expected_hint = TESTCASE_GENERATION_TEMPLATES[
                    index % len(TESTCASE_GENERATION_TEMPLATES)
                ]
                input_payload = {"requirement": raw, "module": "G4"}
            EvaluationCase.objects.create(
                suite=suite,
                case_number=start_number + index + 1,
                task_type=task_type,
                input_payload=input_payload,
                expected_payload={"grading_points": [expected_hint]},
                golden_labels={"must_cover": expected_hint},
                split=case_split(index),
                annotator=DEMO_TAG,
                annotated_at=timezone.now() - timedelta(days=6),
                metadata={"origin": "synthetic", "seed_key": seed_key},
            )
            created += 1
        if created:
            self.stdout.write(f"  评测集「{suite.name}」新增 {created} 条样本")

    def _seed_policy(self, project, actor):
        policy, _ = RetrievalPolicy.objects.get_or_create(
            project=project,
            name=DEMO_POLICY_NAME,
            version=3,
            defaults={
                "is_default": True,
                "is_active": True,
                "config": {
                    "channels": {
                        "dense": 0.35, "sparse": 0.2, "structured": 0.25, "graph": 0.2,
                    },
                    "rrf_k": 60,
                    "rerank": {"enabled": True, "top_n": 20},
                    "mmr": {"enabled": True, "lambda": 0.7},
                    "budget": {"max_candidates": 50, "max_tokens": 8000},
                    "graph_policy": "expand_one_hop",
                },
                "created_by": actor,
            },
        )
        return policy

    def _seed_traces(self, project, actor, policy):
        traces, outputs = [], []
        now = timezone.now()
        policy_version = f"default-policy@v{policy.version}"
        for index, (task_type, query, status, tokens, channels, error_code) in enumerate(
            TRACE_SEEDS, start=1
        ):
            task_id = f"{DEMO_TRACE_PREFIX}{index:02d}"
            occurred = now - timedelta(hours=(len(TRACE_SEEDS) - index) * 5 + 1)
            trace, _ = RetrievalTrace.objects.get_or_create(
                project=project,
                task_id=task_id,
                defaults={
                    "user": actor,
                    "task_type": task_type,
                    "query": query,
                    "rewritten_query": f"{query}（测试环境）",
                    "policy_version": policy_version,
                    "status": status,
                    "channels": {k: {"recalled": v} for k, v in channels.items()},
                    "candidates": [
                        {"doc_id": f"doc-{index}-{n}", "score": round(0.92 - n * 0.07, 3)}
                        for n in range(1, min(6, sum(channels.values())))
                    ],
                    "citations": [] if status != "completed" else [f"doc-{index}-1"],
                    "timings": {"retrieve_ms": 120 + index * 7, "rerank_ms": 80, "total_ms": 260 + index * 9},
                    "token_usage": tokens,
                    "error_code": error_code,
                },
            )
            if trace.created_at:
                RetrievalTrace.objects.filter(pk=trace.pk).update(created_at=occurred)
            traces.append(trace)

            if status != "completed":
                outputs.append(None)
                continue
            content = self._output_content(task_type, query)
            output, _ = GenerationOutput.objects.get_or_create(
                trace=trace,
                defaults={
                    "project": project,
                    "task_type": task_type,
                    "task_id": f"{task_id}-out",
                    "model_version": "qwen3-coder-plus",
                    "prompt_version": "flywheel-prompt@v7",
                    "content": content,
                    "output_hash": sha256_text(content),
                    "metadata": {
                        "policy_version": policy_version,
                        "retrieved_docs": [f"doc-{index}-1", f"doc-{index}-2"],
                        "latency_ms": 260 + index * 9,
                    },
                },
            )
            outputs.append(output)
        return traces, outputs

    @staticmethod
    def _output_content(task_type, query):
        if task_type == "code_review":
            return (
                f"针对「{query}」的审查结论：\n"
                "1) 发现 2 处空指针风险（P1）：参数未做非空校验即解引用。\n"
                "2) 发现 1 处事务边界问题（P1）：跨服务写入缺少补偿逻辑，部分失败会残留脏数据。\n"
                "3) 建议：补 3 个边界用例，覆盖空集合、超长入参与并发重入。"
            )
        if task_type == "testcase_generation":
            return (
                f"依据「{query}」生成 6 条用例：\n"
                "TC-01 正常路径；TC-02 超时后重试成功；TC-03 重试耗尽；\n"
                "TC-04 并发同请求幂等；TC-05 非法参数快速失败；TC-06 断点续传乱序到达。"
            )
        if task_type == "test_execution":
            return (
                f"「{query}」执行结果：用例 128 条，通过 119 条，失败 9 条。\n"
                "失败集中在竞价撮合超时重试路径，与 DEF-2026-1174 关联。"
            )
        return (
            f"针对「{query}」的回答：\n"
            "测试环境域名解析需通过平台通道下发到各执行器容器，容器不继承宿主机 /etc/hosts。\n"
            "参考：《测试环境部署手册》第 2 章。"
        )

    def _seed_feedback(self, project, actor, traces, outputs):
        now = timezone.now()
        for index, (signal, trace_index, reason, comment, actor_type, value) in enumerate(
            FEEDBACK_SEEDS, start=1
        ):
            trace = traces[trace_index]
            output = outputs[trace_index]
            occurred = now - timedelta(hours=len(FEEDBACK_SEEDS) - index + 1)
            event, created = FeedbackEvent.objects.get_or_create(
                idempotency_key=f"{DEMO_FEEDBACK_PREFIX}{index:02d}",
                defaults={
                    "project": project,
                    "trace": trace,
                    "output": output,
                    "signal": signal,
                    "value": value,
                    "reason_code": reason,
                    "comment": comment,
                    "detail": {
                        "source": "workbench",
                        "policy_version": trace.policy_version,
                        "task_type": trace.task_type,
                    },
                    "actor": actor if actor_type == "user" else None,
                    "actor_type": actor_type,
                    "occurred_at": occurred,
                },
            )
            if created:
                FeedbackEvent.objects.filter(pk=event.pk).update(occurred_at=occurred)

    def _seed_runs(self, project, actor, suite, policy):
        """在种子集上造 5 次评测运行；顺序与结果通过显式时间戳固定。

        最新一次（界面默认选中）必须带完整结果，否则「失败样本分析阶段」是空的。
        """
        now = timezone.now()
        policy_version = f"default-policy@v{policy.version}"
        failures = {
            "基线": {3, 11, 19, 27, 35, 43},
            "策略调优后": {3, 19, 41},
            "压测": set(),
            "回归验证": {7},
            "新模型回放": {3, 7, 11, 19, 27, 33, 41, 47, 50},
        }
        # (名称后缀, 状态, 天数前, L0, L1, L2, L3, 成本, 是否铺结果)
        plan = [
            ("基线", "completed", 12, 0.91, 0.88, 0.82, 0.79, 0.42, True),
            ("策略调优后", "completed", 8, 0.94, 0.91, 0.87, 0.85, 0.39, True),
            ("压测", "failed", 5, None, None, None, None, 0.11, False),
            ("回归验证", "running", 2, None, None, None, None, 0.07, False),
            ("新模型回放", "completed", 0, 0.93, 0.90, 0.86, 0.83, 0.36, True),
        ]

        runs = []
        for suffix, status, days_ago, l0, l1, l2, l3, cost, fill in plan:
            name = f"{DEMO_RUN_PREFIX} · {suffix}"
            started = now - timedelta(days=days_ago, hours=1)
            finished = started + timedelta(minutes=26)
            metrics = (
                {"l0_score": l0, "l1_score": l1, "l2_score": l2, "l3_score": l3}
                if l0 is not None else {}
            )
            cost_summary = {"total_usd": cost, "currency": "USD"}
            values = {
                "config": {
                    "policy_version": policy_version,
                    "model_version": "qwen3-coder-plus",
                    "top_k": 20,
                    "executor": "flywheel-replay",
                },
                "status": status,
                "triggered_by": actor,
                "started_at": started,
                "finished_at": finished if status in ("completed", "failed") else None,
                "metrics_summary": metrics,
                "cost_summary": cost_summary,
            }
            run, created = EvaluationRun.objects.get_or_create(
                suite=suite, name=name, defaults=values
            )
            if not created:
                for field, value in values.items():
                    setattr(run, field, value)
                run.save(update_fields=list(values.keys()) + ["updated_at"])
            # created_at 是 auto_now_add，只能显式回填以固定列表顺序
            EvaluationRun.objects.filter(pk=run.pk).update(created_at=started)
            run.refresh_from_db()
            runs.append(run)

            if fill:
                self._seed_results(run, suite, failures[suffix], policy_version)
        return runs

    def _seed_results(self, run, suite, fail_case_numbers, policy_version):
        cases = list(EvaluationCase.objects.filter(suite=suite).order_by("case_number"))
        if not cases:
            self.stdout.write(self.style.WARNING(f"  评测集 {suite.name} 没有样本，跳过结果生成"))
            return
        base_l3 = (run.metrics_summary or {}).get("l3_score") or 0.83
        created = 0
        for case in cases:
            number = case.case_number
            if number in fail_case_numbers:
                jitter = stable_ratio(run.name, number, "fail")
                scores = {
                    "l0": round(0.60 + 0.20 * jitter, 2),
                    "l1": round(0.36 + 0.12 * jitter, 2),
                    "l2": round(0.22 + 0.16 * jitter, 2),
                    "l3": round(0.16 + 0.12 * jitter, 2),
                }
                error_message = (
                    f"用例 #{number} 判别依据不足：L2 得分 {scores['l2']:.2f}、"
                    f"L3 得分 {scores['l3']:.2f}，均低于阈值 {FAILURE_THRESHOLDS['l2']:.2f}"
                )
            else:
                jitter = stable_ratio(run.name, number, "pass")
                drift = 0.06 * (jitter - 0.5)
                scores = {
                    "l0": round(min(1.0, 0.94 + drift), 2),
                    "l1": round(min(1.0, 0.91 + drift), 2),
                    "l2": round(min(1.0, 0.88 + drift), 2),
                    "l3": round(min(1.0, max(0.5, base_l3 + 0.10 * (jitter - 0.4))), 2),
                }
                error_message = ""
            _, is_new = EvaluationResult.objects.update_or_create(
                run=run,
                case=case,
                defaults={
                    "status": "completed",
                    "predicted_payload": {
                        "level": "L2",
                        "summary": "AI 输出已与期望要点比对",
                        "hit": error_message == "",
                    },
                    "latency_ms": 900 + int(jitter * 2600),
                    "token_usage": 700 + int(jitter * 1800),
                    "estimated_cost_usd": round(0.004 + jitter * 0.01, 4),
                    "l0_score": scores["l0"],
                    "l1_score": scores["l1"],
                    "l2_score": scores["l2"],
                    "l3_score": scores["l3"],
                    "raw_scores": {
                        "thresholds": FAILURE_THRESHOLDS,
                        "policy_version": policy_version,
                        "per_level": scores,
                    },
                    "error_message": error_message,
                },
            )
            created += int(is_new)
        self.stdout.write(
            f"  运行「{run.name}」结果 {len(cases)} 条（新增 {created}），"
            f"失败样本 {len(fail_case_numbers)} 条"
        )

    def _seed_candidates(self, project, actor, snapshot, runs):
        asset_by_key = {
            a.key: a for a in KnowledgeAsset.objects.filter(
                project=project, key__startswith=DEMO_ASSET_PREFIX
            )
        }
        accepted_asset_keys = [
            f"{DEMO_ASSET_PREFIX}rule/test-env-dns",
            f"{DEMO_ASSET_PREFIX}fact/e-voting-capacity",
        ]
        accepted_index = 0
        latest_run = runs[-1] if runs else None
        created = 0

        for index, (kind, origin, level, confidence, state, payload, review_reason) in enumerate(
            CANDIDATE_SEEDS, start=1
        ):
            normalized = f"{kind}|{payload.get('title')}"
            dedup_key = KnowledgeCandidate.build_dedup_key(project.id, kind, normalized)
            promoted_asset = None
            if state == "accepted" and accepted_index < len(accepted_asset_keys):
                promoted_asset = asset_by_key.get(accepted_asset_keys[accepted_index])
                accepted_index += 1

            evidence = [{
                "type": "source_snapshot",
                "snapshot_id": str(snapshot.id),
                "excerpt": payload.get("statement", "")[:80],
            }]
            if origin == "evaluation_failure" and latest_run is not None:
                evidence.append({
                    "type": "evaluation_run",
                    "run_id": str(latest_run.id),
                    "suite": latest_run.suite.name,
                })

            _, is_new = KnowledgeCandidate.objects.update_or_create(
                dedup_key=dedup_key,
                defaults={
                    "project": project,
                    "kind": kind,
                    "origin": origin,
                    "payload": payload,
                    "level": level,
                    "confidence": confidence,
                    "source_snapshot": snapshot,
                    "evidence": evidence,
                    "state": state,
                    "promoted_asset": promoted_asset,
                    "extracted_by": DEMO_TAG,
                    "prompt_version": "flywheel-prompt@v7",
                    "review_reason": review_reason,
                    "reviewed_by": actor if state in ("accepted", "rejected", "merged") else None,
                    "reviewed_at": (
                        timezone.now() - timedelta(days=1)
                        if state in ("accepted", "rejected", "merged") else None
                    ),
                    "created_by": actor,
                },
            )
            created += int(is_new)
        self.stdout.write(f"  知识候选 {len(CANDIDATE_SEEDS)} 条（新增 {created}）")

    # ------------------------------------------------------------------ 报告
    def _report(self, project):
        suites = EvaluationSuite.objects.filter(project=project)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== 演示数据现状 ==="))
        for suite in suites:
            run_count = EvaluationRun.objects.filter(suite=suite).count()
            self.stdout.write(
                f"  评测集 {suite.name}: {suite.cases.count()} 用例 / {run_count} 次运行"
            )
        self.stdout.write(f"  检索轨迹 {RetrievalTrace.objects.filter(project=project).count()} 条")
        self.stdout.write(f"  生成输出 {GenerationOutput.objects.filter(project=project).count()} 条")
        self.stdout.write(f"  反馈事件 {FeedbackEvent.objects.filter(project=project).count()} 条")
        self.stdout.write(f"  知识候选 {KnowledgeCandidate.objects.filter(project=project).count()} 条")

        # 与后端失败判据做一次一致性自检：界面显示的失败样本数应等于桥接器结果
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== 失败样本判据一致性自检 ==="))
        for run in EvaluationRun.objects.filter(suite__project=project, name__startswith=DEMO_RUN_PREFIX):
            total = run.results.count()
            if not total:
                continue
            bridge_failures = len(EvaluationReviewBridge(run).find_failures())
            low_score = 0
            for result in run.results.all():
                scores = [
                    getattr(result, f"{level}_score")
                    for level in FAILURE_THRESHOLDS
                ]
                if any(s is not None and s < FAILURE_THRESHOLDS[lv]
                       for lv, s in zip(FAILURE_THRESHOLDS, scores)):
                    low_score += 1
            flag = "一致" if bridge_failures == low_score else "不一致！"
            self.stdout.write(
                f"  {run.name}: 样本 {total} / 桥接器判失败 {bridge_failures} / 低分样本 {low_score} → {flag}"
            )
