"""任务 17–19：防腐检查、联合链路图和运营指标。"""
import logging
from collections import Counter

from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from projects.models import ProjectMember

from .capability_models import CapabilityRelease
from .capability_registry import (
    ALL_WORKFLOW_STAGES,
    LEGACY_WORKFLOW_STAGES,
    WORKFLOW_STAGES,
    is_evolvable,
)
from .evaluation_models import EvaluationResult, EvaluationRun
from .graph import GraphEdgeSpec, GraphNodeSpec, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import IndexProjection, KnowledgeConflict, KnowledgeVersion
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .gold_models import GoldDataset
from .workflow_models import (
    GATE_CONFIRMABLE_STATES,
    GATE_HUMAN_FINAL_STATES,
    GATE_PASSING_STATES,
    GATE_SCORABLE_STATES,
    WorkflowSkillLock,
    WorkflowStageGate,
)

logger = logging.getLogger(__name__)

#: 新流程的默认阶段序列。真值源在 ``capability_registry``——这里只做一次搬运，
#: 不允许在本文件另写一份字面量，否则"主链路是哪四个阶段"又会有两个答案。
DEFAULT_WORKFLOW_STAGE_ORDER = list(WORKFLOW_STAGES)

#: 历史流程的阶段序列（短期中间模板期间发起）。**只为读懂存量数据而存在**，
#: 新能力不要往它上面挂；它退场的条件只有一个：存量流程都跑完并归档。
LEGACY_WORKFLOW_STAGE_ORDER = list(LEGACY_WORKFLOW_STAGES)

#: 兼容别名。历史代码与测试都按这个名字取"链路阶段序列"。
#: 语义已收窄为「**新流程的默认**序列」：
#: - 判断"某个 stage 算不算链路阶段" → 用 ``ALL_WORKFLOW_STAGE_SET``；
#: - 读某条**既有**流程的阶段序列 → 用 ``WorkflowGateService.stage_order_for``。
WORKFLOW_STAGE_ORDER = DEFAULT_WORKFLOW_STAGE_ORDER

#: 全部出现过的链路阶段。判定"是不是链路阶段"必须用它：只认默认序列会把
#: 存量流程的产出当成旁路产出，门禁、版本锁与进度都会凭空消失。
ALL_WORKFLOW_STAGE_SET = frozenset(ALL_WORKFLOW_STAGES)

#: 收口阶段 = 链路的最后一段：新流程是「报告产出」，存量流程是「问题跟踪」。
#: 报告契约只对收口阶段生效，两边都要认；只认新的会让存量流程的报告不再被校验。
CLOSING_STAGES = frozenset({
    DEFAULT_WORKFLOW_STAGE_ORDER[-1], LEGACY_WORKFLOW_STAGE_ORDER[-1],
})


def _without_timestamp(payload: dict) -> dict:
    """去掉 ``checked_at`` 后比对，用来判断结论本身有没有变。"""
    return {key: value for key, value in (payload or {}).items() if key != "checked_at"}


class WorkflowGateService:
    """统一维护四阶段 Skill 门禁；前端状态和协议校验都读取同一真值。"""

    @classmethod
    def stage_order_for(cls, project_id, workflow_id: str, *, stage: str = "") -> list:
        """解析**某一条**流程实际使用的阶段序列。

        为什么需要它：主链路口径在 2026-10-02 变过一次（测试方案/报告生成 让位给
        风险识别/问题跟踪）。如果读流程时一律套用新序列，那么所有存量流程都会
        突然"少两个阶段"——进度、门禁留痕、版本锁全对不上，等于把历史数据变成砖。

        判据取该流程**自己的留痕**，而不是一个全局开关：

        - 出现 ``test_plan_generation`` / ``report_generation`` 任一 → 历史序列。
          这两个阶段只存在于历史序列，命中即为铁证；``start_workflow`` 当年一次性
          把四个阶段全锁了，所以只要流程是正常发起的，留痕一定包含它们。
        - 其余 → 新序列（包含"刚发起、还没有任何留痕"的新流程）。

        Args:
            stage: 调用方**正在操作的那个阶段**。它是第三份证据：如果它只存在于
                历史序列，说明调用方本来就是按历史序列在走这条流程（旁路产出、
                补数脚本、人工补录都会这样进来）。少了这条，一条没有任何留痕的
                流程去问"报告阶段能不能进"会被当成"该阶段不属于本流程"而跳过
                越阶段校验——校验静默失效比报错难查得多。

        最后会把"出现过但不在序列里"的阶段补到末尾：宁可顺序看着奇怪，
        也不能让一个真实存在过的阶段从列表里消失。
        """
        present: set[str] = set()
        if workflow_id:
            present.update(
                WorkflowSkillLock.objects
                .filter(project_id=project_id, workflow_id=workflow_id)
                .exclude(stage="")
                .values_list("stage", flat=True)
            )
            present.update(
                WorkflowStageGate.objects
                .filter(project_id=project_id, workflow_id=workflow_id)
                .values_list("stage", flat=True)
            )
            present.update(
                GenerationOutput.objects
                .filter(project_id=project_id)
                .filter(metadata__protocol__workflow_id=workflow_id)
                .values_list("task_type", flat=True)
            )
        legacy_marks = set(LEGACY_WORKFLOW_STAGE_ORDER) - set(DEFAULT_WORKFLOW_STAGE_ORDER)
        asked_stage = str(stage or "")
        is_legacy = bool(present & legacy_marks) or (
            bool(asked_stage) and asked_stage in legacy_marks
        )
        order = list(LEGACY_WORKFLOW_STAGE_ORDER if is_legacy else DEFAULT_WORKFLOW_STAGE_ORDER)
        for name in ALL_WORKFLOW_STAGES:
            if name in present and name not in order:
                order.append(name)
        return order

    @classmethod
    def stage_catalog(cls, *, project, stages=None) -> dict:
        """发起流程向导的数据源：**按阶段**列出可选的 Skill 包。

        向导第一步要让用户"搜索并选定每个阶段用哪个包"，所以这里必须给出：

        1. 每个阶段的**默认包**——沿用平台按 manifest 声明解析的结果，
           让不想逐个挑的人一路「下一步」就能发起。默认包取不到（没有包声明该阶段）
           时为 None，页面要如实显示"没有已声明该阶段的包"，而不是拿别的包顶替。
        2. 项目里**全部可运行的包**（有一条 active 版本），带上版本号与包哈希；
           声明了该阶段的排在前面。前端在本地按关键词过滤，不再多打一次接口。

        刻意**不按 manifest 声明硬筛候选**：主链路刚把阶段换成风险识别/问题跟踪，
        现存包声明的还是旧阶段，硬筛会让向导在这两个阶段一个候选都给不出来——
        而"人选了它"本来就是比"包里写了什么"更强的事实（见 ``bind_stage``）。

        包不可运行（没有 active 版本）时仍然列出来，标 ``runnable=False``：
        让人看见"这个包在、但还不能用"，比它凭空消失更好排查。
        """
        from skills.models import Skill, SkillVersion
        from skills.runtime import SkillRuntimeResolver

        project_id = getattr(project, "pk", project)
        targets = list(stages or DEFAULT_WORKFLOW_STAGE_ORDER)
        unknown = [item for item in targets if item not in ALL_WORKFLOW_STAGE_SET]
        if unknown:
            raise ValidationError(f"未知的阶段：{'、'.join(unknown)}")

        # 声明阶段要**连候选版本一起看**：一个包可能已经声明了阶段、
        # 只是还没激活版本。此时它的声明仍然是有用信息（"这个包本就打算干这件事"），
        # 只读活跃版本会让向导把它算成"没有包声明本阶段"，于是默认项空着。
        latest_declared: dict[str, str] = {}
        for skill_id, manifest in (
            SkillVersion.objects
            .filter(skill__project_id=project_id)
            .order_by("created_at")
            .values_list("skill_id", "manifest")
        ):
            stage = str((manifest or {}).get("stage") or "")
            if stage:
                latest_declared[str(skill_id)] = stage

        skills: list[dict] = []
        for skill in (
            Skill.objects
            .filter(project_id=project_id)
            .select_related("active_version", "active_version__release", "active_version__skill")
            .order_by("name")
        ):
            # 用运行时同一个解析器取"当前会用哪一版"：判据只能有一处定义，
            # 否则页面会承诺"选它就能锁上"而实际锁不上（或反过来）。
            # 注意它优先活跃版本、没有活跃版本时退到最新可运行版本——
            # 未激活的包同样能跑，激活只是可选的钉版手段。
            version = SkillRuntimeResolver.runnable_version(skill)
            manifest = (getattr(version, "manifest", None) or {}) if version else {}
            release = getattr(version, "release", None) if version else None
            declared = str(manifest.get("stage") or "") or latest_declared.get(str(skill.pk), "")
            runnable = version is not None
            skills.append({
                "skill_id": str(skill.pk),
                "skill_name": skill.name,
                "description": (skill.description or "").strip(),
                "declared_stage": declared,
                "declared_stage_label": cls.STAGE_LABELS.get(declared, declared),
                "runnable": runnable,
                "skill_version_id": str(version.pk) if version else "",
                "version": str(version.version) if version else "",
                "release_state": release.state if release is not None else "",
                "package_sha256": str(version.package_sha256) if version else "",
                "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
            })

        stages_payload = []
        for stage in targets:
            declared = [item for item in skills if item["declared_stage"] == stage]
            stages_payload.append({
                "stage": stage,
                "label": cls.STAGE_LABELS.get(stage, stage),
                "default": next((item for item in declared if item["runnable"]), None),
                "declared_skill_ids": [item["skill_id"] for item in declared],
                "runnable_count": sum(1 for item in skills if item["runnable"]),
            })
        return {
            "stage_order": targets,
            # 当前链路在前，历史模板的阶段也列出来：向导里能选到它们，
            # 用过历史模板的流程才谈得上继续推进。
            "all_stage_order": list(ALL_WORKFLOW_STAGES),
            "stages": stages_payload,
            "skills": skills,
        }

    @classmethod
    def register_output(cls, output):
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id")
        stage = protocol.get("stage") or output.task_type
        if not workflow_id or stage not in ALL_WORKFLOW_STAGE_SET:
            return None
        # 无论门禁走到哪一步，都先把这一阶段的版本锁定补上：
        # 流水线若由 ``start_workflow`` 启动，锁在入口就已存在（幂等返回）；
        # 若历史链路或旁路调用直接产出了结果，这里补锁，产出才谈得上版本溯源。
        cls.ensure_stage_lock(output, workflow_id=workflow_id, stage=stage)
        gate = WorkflowStageGate.objects.filter(
            project=output.project, workflow_id=workflow_id, stage=stage
        ).first()
        if gate is not None and gate.output_id == output.pk:
            # 同一个产出的重复登记必须幂等。早期实现无条件
            # ``update_or_create(defaults={"status": "pending", ...})``，
            # 于是"同一批结果重发一次"会把一个**已通过**的门禁打回待测评，
            # 后一阶段随即被挡住——而系统里并没有任何新信息。
            cls._sync_report_contract(gate)
            return gate
        if gate is not None:
            # 换了产出才算"这一阶段重跑过"，此时重置为待测评是正确的：
            # 新内容必须重新过门禁，旧结论不能顺延。
            gate.output = output
            gate.status = "pending"
            gate.scores = {}
            gate.detail = {}
            gate.reason = ""
            gate.decided_by = None
            gate.decided_at = None
            gate.save(update_fields=[
                "output", "status", "scores", "detail", "reason",
                "decided_by", "decided_at", "updated_at",
            ])
            cls._sync_report_contract(gate)
            return gate
        gate = WorkflowStageGate.objects.create(
            project=output.project, workflow_id=workflow_id, stage=stage,
            output=output, status="pending",
        )
        cls._sync_report_contract(gate)
        return gate

    @staticmethod
    def _sync_report_contract(gate) -> dict | None:
        """把报告契约校验结果写进门禁 ``detail``（仅报告阶段）。

        刻意**不改变门禁状态**：登记只负责"把证据摆出来"，放行与否由 ``evaluate``
        决定。这样页面上能在有人点"评测"之前就看见报告缺了什么，
        而不会因为一次登记动作就悄悄把阶段判死。

        校验是确定性的纯函数，重复登记同一产出时重算的代价可以忽略；
        但结果完全一致，所以幂等性不受影响。
        """
        from .report_gates import ReportGateService

        if gate is None or gate.output_id is None:
            return None
        if gate.stage not in CLOSING_STAGES:
            return None
        result = ReportGateService.validate(gate.output)
        # ``checked_at`` 每次都不同，比对时排掉，否则"没变也总是写一次"，
        # 让"只在结论变化时落库"这句话变成假的。
        previous = (gate.detail or {}).get("report_contract") or {}
        if _without_timestamp(previous) != _without_timestamp(result):
            gate.detail = {**(gate.detail or {}), "report_contract": result}
            gate.save(update_fields=["detail", "updated_at"])
        return result

    @staticmethod
    def ensure_stage_lock(output, *, workflow_id="", stage="") -> bool:
        """确保该阶段的 Skill 版本锁存在；返回是否确实锁定了版本。

        刻意**不抛异常**：走到这里说明该阶段的入口门禁已经放行过，能力包当时是可用的。
        若此刻解析失败（例如并发场景下版本刚被隔离），正确做法是"不声称锁定了版本"
        并留一条告警，而不是把一份已经生成好的产出丢掉——丢产出的代价远大于
        "这一条产出没有版本溯源"，而且后者在页面上是可见的。
        """
        from .task_binding import SkillBindingRefused, TaskSkillBindingService

        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = workflow_id or str(protocol.get("workflow_id") or "")
        stage = stage or str(protocol.get("stage") or output.task_type or "")
        if not workflow_id or stage not in ALL_WORKFLOW_STAGE_SET:
            return False
        if WorkflowSkillLock.objects.filter(
            project_id=output.project_id, workflow_id=workflow_id, lock_key=f"stage:{stage}"
        ).exists():
            return True
        try:
            binding = TaskSkillBindingService.bind_stage(
                project=output.project, workflow_id=workflow_id, stage=stage, actor=None,
            )
        except SkillBindingRefused as exc:
            logger.warning(
                "阶段 %s 的 Skill 版本未能锁定（产出 %s 将以「无版本溯源」记录）：%s",
                stage, output.pk, exc,
            )
            return False
        return bool(binding.get("managed"))

    @staticmethod
    def assert_can_enter(project_id, workflow_id, stage):
        if stage not in ALL_WORKFLOW_STAGE_SET:
            return
        # 前置关系必须按**该流程自己的序列**算：套用新序列会让存量流程的
        # test_plan_generation 变成"不在序列里"，于是越阶段校验静默失效。
        order = WorkflowGateService.stage_order_for(project_id, workflow_id, stage=stage)
        if stage not in order:
            return
        position = order.index(stage)
        if position == 0:
            return
        previous = order[position - 1]
        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=previous
        ).first()
        if gate is not None and gate.status in GATE_PASSING_STATES:
            return
        # 提示必须能直接指向下一步动作，而不是只说"没通过"——运维看到"没通过"
        # 既不知道是没人评过、还是评了没过、还是可以找负责人放行。
        if gate is None:
            raise ValidationError(
                f"上一阶段 {previous} 尚未产生产出，无法进入 {stage}；"
                f"请先完成该阶段并提交门禁测评"
            )
        if gate.status == "failed":
            raise ValidationError(
                f"上一阶段 {previous} 门禁未通过（{gate.reason or '分层评测未达标'}），"
                f"无法进入 {stage}；请修复后重跑该阶段，或由测试负责人留痕放行"
            )
        if gate.status == "unscored":
            # 无评分不等于不通过：可能是这一阶段还没接评测，也可能是评测结果
            # 全是"无信号"。此时链路**不应该被卡死**——评分可以后补，业务推进
            # 不能等。所以这里的提示要给出一条可执行的路（人工确认），
            # 而不是只说"状态是 unscored"。
            raise ValidationError(
                f"上一阶段 {previous} 暂无可用评分（{gate.reason or '没有已完成的评测结果'}），"
                f"无法进入 {stage}；请先完成评测，或由测试负责人点击「人工确认」放行"
            )
        raise ValidationError(
            f"上一阶段 {previous} 门禁尚未测评（当前状态：{gate.get_status_display()}），"
            f"无法进入 {stage}；请先运行门禁测评，或由测试负责人点击「人工确认」放行"
        )

    @staticmethod
    def is_human_decided(gate) -> bool:
        """这一阶段是否已有**人工拍板**的结论。

        三个来源，任一成立即为真：

        1. ``confirmed``——人工确认放行（无评分）；
        2. ``overridden``——负责人强制放行；
        3. ``detail.manual_score`` 存在——人工评分给出的结论（状态可能是
           ``passed``/``failed``，光看 status 分不出"机器评的"还是"人评的"，
           所以判据落在留痕上）。

        判它干什么：**自动重算（``evaluate``）不得推翻人工结论**。产出若换了，
        ``register_output`` 会把 ``detail`` 清空、状态打回 ``pending``，
        所以"新内容必须重新过门禁"这条铁律依然成立；但在产出没变的情况下，
        一次"重算"不该把"人已经评过/放行过"抹掉——那会让页面出现莫名其妙的回退，
        下一阶段跟着中断。
        """
        if gate.status in GATE_HUMAN_FINAL_STATES:
            return True
        return bool((gate.detail or {}).get("manual_score"))

    @staticmethod
    def evaluate(gate, actor=None):
        # T16：收口阶段先过**确定性契约**，再谈分层评分。
        # 顺序不能反：一份引用了不存在产出、或压根没写"未闭环问题"的收口产出，
        # 即便文案评分满分也不该放行——它的分数建立在无法核验的依据上。
        # 契约不过就直接判失败并说明缺什么，避免"分数很高但内容是空的"这种放行。
        human_decided = WorkflowGateService.is_human_decided(gate)
        if gate.stage in CLOSING_STAGES and gate.output_id:
            from .report_gates import ReportGateService

            contract = ReportGateService.validate(gate.output)
            gate.detail = {**(gate.detail or {}), "report_contract": contract}
            if not contract["ok"] and not human_decided:
                gate.scores = {}
                gate.status = "failed"
                gate.reason = "报告契约校验未通过：" + "；".join(
                    contract["errors"] or ["未说明原因"]
                )
                gate.decided_by = actor
                gate.decided_at = timezone.now()
                gate.save(update_fields=[
                    "scores", "detail", "status", "reason",
                    "decided_by", "decided_at", "updated_at",
                ])
                return gate

        results = list(EvaluationResult.objects.filter(
            case__source_output=gate.output, status="completed"
        ).order_by("-updated_at")) if gate.output_id else []
        values = {f"l{level}": [] for level in range(4)}
        for result in results:
            for level in range(4):
                score = getattr(result, f"l{level}_score")
                if score is not None:
                    values[f"l{level}"].append(float(score))
        scores = {
            key: round(sum(items) / len(items), 4)
            for key, items in values.items() if items
        }
        if human_decided:
            # 人拍板/人工评分的结论不能被一次"重算"推翻（判据见 ``is_human_decided``）。
            # 产出若真的换了，``register_output`` 早已清空 ``detail`` 并把状态打回
            # ``pending``，所以这里保留的是"对同一份产出的人工结论"。
            #
            # ``scores`` **也不覆盖**：状态是 passed 而 scores 被清成空，
            # 页面会显示"已通过但无分数"，解释不通——人工分才是这个状态的依据，
            # 留在 ``scores`` 里；机器算出来的分挪到 ``detail.auto_scores`` 当补充证据。
            gate.detail = {**(gate.detail or {}), "auto_scores": scores}
            gate.save(update_fields=["detail", "updated_at"])
            return gate
        gate.scores = scores
        if scores:
            gate.status = "passed" if all(
                score >= gate.threshold for score in scores.values()
            ) else "failed"
            gate.reason = "分层评测自动计算"
        else:
            # "没有评分"与"评了不达标"是两件完全不同的事，不能都落到 ``failed``：
            # 前者是*信号缺失*（还没接评测，或评测结果全是"无信号"），后者是*结论为负*。
            # 合并会让链路被一条并不存在的负面结论挡住；两者的修复动作也不同——
            # ``unscored`` 是补评测或人工确认，``failed`` 是修复后重跑。
            gate.status = "unscored"
            gate.reason = "没有可用于门禁判断的已完成评测结果（无评分，可由测试负责人人工确认放行）"
        gate.decided_by = actor
        gate.decided_at = timezone.now()
        # ``detail`` 必须在 update_fields 里：报告阶段的契约结论在本次调用中已写进
        # ``gate.detail``，漏掉它会让"校验通过时的结论"静默丢失，
        # 页面随后显示"没有契约证据"，而门禁显示已通过——两处对不上。
        gate.save(update_fields=[
            "scores", "detail", "status", "reason", "decided_by", "decided_at", "updated_at",
        ])
        return gate

    @staticmethod
    def override(gate, actor, reason):
        if not reason.strip():
            raise ValidationError("负责人强制放行必须填写原因")
        gate.status = "overridden"
        gate.reason = reason.strip()
        gate.decided_by = actor
        gate.decided_at = timezone.now()
        gate.save(update_fields=["status", "reason", "decided_by", "decided_at", "updated_at"])
        return gate

    @staticmethod
    def confirm(gate, actor, reason=""):
        """人工确认放行：不要求先有评分，确认后直接进入下一阶段。

        为什么需要它：``evaluate`` 把"没有评分"如实落成 ``unscored`` 之后，这一阶段
        仍然会挡住下一阶段。但**"评分挂起"与"链路可推进"应当能同时成立**——评测
        可能还没接通、金标可能还没攒够，而业务推进不能等评测。人工确认就是那条出口，
        且它必须**留痕**（谁、什么时候、为什么），否则"有人确认过"和"没人管"
        在事后完全分不出来。

        与 ``override`` 的区别是语义强度，不是实现：
        - ``confirm``：**尚无结论**（待测评 / 无评分）时由人拍板放行，默认原因即可；
        - ``override``：**已有负面结论**（failed）后强制放行，必须填写原因。

        因此这里刻意**不允许** ``failed`` 走确认：评测已经判了"不达标"时，
        一次普通点击不该把它变成"已通过"，该走强制放行这条更重的留痕路径。
        """
        if gate.status not in GATE_CONFIRMABLE_STATES:
            raise ValidationError(
                f"当前状态为「{gate.get_status_display()}」，不能人工确认；"
                f"仅「待测评」「无评分」可确认。评测未通过的请走负责人强制放行。"
            )
        gate.status = "confirmed"
        gate.reason = reason.strip() or "测试负责人人工确认放行（无评分）"
        gate.decided_by = actor
        gate.decided_at = timezone.now()
        gate.save(update_fields=["status", "reason", "decided_by", "decided_at", "updated_at"])
        return gate

    @staticmethod
    def score(gate, actor, score, reason="", threshold=None):
        """人工评分：把人对这一阶段的判断落成门禁里可统计的分数。

        三者的分工（都在同一张 ``WorkflowStageGate`` 上，靠 status 分流）：

        - ``evaluate``：**机器**算分（分层评测 l0–l3），全部分项 >= 阈值才通过；
        - ``score``：**人**给一个总分，落成 ``scores={"manual": 0-1}``，
          再按阈值判 passed / failed——所以人工评分**同样会有"不达标"**，
          不会因为是人打的就一律放行；
        - ``confirm``：不打分、直接放行，用于"这一阶段根本还没法评"，
          而不是"我评过了，结论是合格"。

        刻度：**对外百分制、对内 0–1**。库里的 ``scores`` 与自动评测保持同一量纲，
        否则 ``threshold``（默认 0.7）会在两套刻度下被两种解释同时使用——
        同一个字段两种含义，是后面所有统计口径分歧的起点。
        """
        if score is None or score == "":
            raise ValidationError("人工评分必须提供分数")
        try:
            value = float(score)
        except (TypeError, ValueError):
            raise ValidationError("人工评分必须是 0–100 之间的数字")
        if value < 0 or value > 100:
            raise ValidationError("人工评分必须在 0–100 之间")
        if gate.status not in GATE_SCORABLE_STATES:
            raise ValidationError(
                f"当前状态为「{gate.get_status_display()}」，已由人工拍板放行，不能再评分；"
                f"确需改判请先重新运行门禁测评"
            )
        limit = float(gate.threshold if threshold is None else threshold)
        normalized = round(value / 100.0, 4)
        note = (reason or "").strip()
        gate.scores = {**(gate.scores or {}), "manual": normalized}
        gate.status = "passed" if normalized >= limit else "failed"
        gate.reason = (
            f"人工评分 {value:g}/100（阈值 {limit * 100:g}）"
            + (f"：{note}" if note else "")
        )
        gate.decided_by = actor
        gate.decided_at = timezone.now()
        # 留痕必须能区分"机器评的"和"人评的"：只看 status=passed 分不出来，
        # 而这两者的可信度与后续责任归属完全不同（见 ``is_human_decided``）。
        gate.detail = {**(gate.detail or {}), "manual_score": {
            "value": value,
            "normalized": normalized,
            "threshold": limit,
            "by": getattr(actor, "username", "") or "",
            "at": gate.decided_at.isoformat(),
            "note": note,
        }}
        gate.save(update_fields=[
            "scores", "detail", "status", "reason", "decided_by", "decided_at", "updated_at",
        ])
        return gate

    # ------------------------------------------------- T15：链路启动与状态真值

    #: 阶段中文名，供前端与错误提示共用，避免各处自己写一份。
    #: 历史阶段（test_plan_generation / report_generation）保留在这里：
    #: 存量流程仍要显示它们的名字，删掉就会变成裸英文 key。
    STAGE_LABELS = {
        "risk_identification": "风险识别",
        "testcase_generation": "测试用例",
        "test_execution": "测试执行",
        "issue_tracking": "问题跟踪",
        "test_plan_generation": "测试方案",
        "report_generation": "报告生成",
    }

    #: 每个阶段的**执行通道真值表**：这一阶段由谁跑、跑到哪里去跑。
    #:
    #: 写这张表是因为平台**没有**四个阶段的通用执行器，只有测试执行有平台内实现
    #: （``testcases`` 的 TestExecution + Celery，且必须先选用例套件）；
    #: 风险识别 / 用例 / 问题跟踪三个阶段没有可被服务端直接调用的生成实现，它们的产出
    #: 由 agent 经 ``/orchestrator/agent-loop/`` 提交后按协议回写。
    #:
    #: 这件事必须落在后端一张表里，而不是前端按阶段名写 if-else：否则"哪些阶段能
    #: 在平台内跑"这个事实会散在前端，后端将来补上某个阶段的执行器时，页面不会跟着变，
    #: 于是又出现"后端能跑、页面说不能"的两套说法。
    STAGE_EXECUTION_CHANNELS = {
        "risk_identification": {
            "channel": "agent",
            "module_key": "risk_identification",
            "entry": "LLM对话",
            "hint": "由 agent 基于需求/规格说明识别高风险点，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "issue_tracking": {
            "channel": "agent",
            "module_key": "issue_tracking",
            "entry": "LLM对话",
            "hint": "由 agent 汇总问题清单与闭环状态，产出按统一协议回写本流程后本阶段自动亮起",
        },
        # ---- 以下两个通道只为**存量流程**保留：它们已不在新主链表里，
        # 但历史流程仍可能停在这两个阶段，页面上的「执行本阶段」不能因为
        # 一次口径变更就变成"没有通道"。
        "test_plan_generation": {
            "channel": "agent",
            "module_key": "test_plan_generation",
            "entry": "LLM对话",
            "hint": "由 agent 产出测试方案，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "report_generation": {
            "channel": "agent",
            "module_key": "report_generation",
            "entry": "LLM对话",
            "hint": "由 agent 产出测试报告，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "testcase_generation": {
            "channel": "agent",
            "module_key": "testcase_generation",
            "entry": "LLM对话",
            "hint": "由 agent 产出测试用例，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "test_execution": {
            "channel": "platform",
            "entry": "测试执行",
            "hint": (
                "在测试执行页选用例套件发起执行；必须带上本流程的 workflow_id，"
                "否则不校验前置阶段、产出也无法归属到本流程"
            ),
        },
    }

    @classmethod
    def start_workflow(cls, *, project, workflow_id, actor=None, stages=None,
                       allow_unmanaged: bool = True, pins: dict | None = None) -> dict:
        """启动四阶段流水线：**逐阶段选定 Skill 包，并一次性锁定四个阶段的版本**。

        为什么在启动时就把四个阶段全锁掉，而不是走到哪锁到哪：

        1. 链路跑起来之后随时可能有人激活新版本。如果每阶段等它自己开始时才解析，
           同一条流水线的四个阶段可能分别用到四份不同的包，"这条链路的产出对应哪个版本"
           就无法回答，回滚也界定不了影响范围。
        2. 启动时一次性解析，能把"某个阶段的能力包没准备好"在**入口处**暴露出来。
           否则问题要到跑了两小时之后的收口阶段才炸，前面阶段的算力与人工全部白费。

        Args:
            pins: ``{stage: skill_id}``——发起流程向导里人**逐阶段选定**的包。
                给了某阶段的包就以它为准，不再按 manifest 声明去筛阶段（新链路阶段
                目前还没有包声明过，靠声明筛会一律落到"未登记"）。没给的阶段
                沿用原有行为：按阶段解析当前可运行版本（有活跃版本则优先用它）。
                无论走哪条路，**绑定不到可运行版本才拒绝**——"没激活"不是拒绝理由，
                包被停用/隔离/校验驳回才是。

        Returns:
            锁定结果；``unmanaged_stages`` 列出没有锁定到 Skill 版本的阶段——
            这些阶段按平台默认行为执行、没有版本溯源，页面要能看到这个事实。
        """
        from .task_binding import TaskSkillBindingService

        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise ValidationError("启动四阶段流水线必须提供 workflow_id")
        targets = list(stages or DEFAULT_WORKFLOW_STAGE_ORDER)
        unknown = [item for item in targets if item not in ALL_WORKFLOW_STAGE_SET]
        if unknown:
            raise ValidationError(f"未知的阶段：{'、'.join(unknown)}")

        requested_pins = {
            str(stage): value for stage, value in (pins or {}).items() if value
        }
        unknown_pins = [s for s in requested_pins if s not in ALL_WORKFLOW_STAGE_SET]
        if unknown_pins:
            raise ValidationError(f"指定的阶段不存在：{'、'.join(unknown_pins)}")
        requested_pins = {s: v for s, v in requested_pins.items() if s in targets}

        bindings: dict[str, dict] = {}
        # 按链路顺序遍历（ALL_WORKFLOW_STAGES 已是"新链路在前、历史阶段在后"），
        # 这样即使有人显式指定历史阶段，顺序也仍是确定的。
        for stage in [s for s in ALL_WORKFLOW_STAGES if s in targets]:
            pinned_skill = requested_pins.get(stage)
            binding = TaskSkillBindingService.bind_stage(
                project=project, workflow_id=workflow_id, stage=stage,
                actor=actor, allow_unmanaged=allow_unmanaged,
                skill=pinned_skill, allow_stage_mismatch=bool(pinned_skill),
            )
            bindings[stage] = binding
        return {
            "workflow_id": workflow_id,
            "stage_order": list(DEFAULT_WORKFLOW_STAGE_ORDER),
            "bindings": {
                stage: cls._binding_view(binding) for stage, binding in bindings.items()
            },
            "locked_stages": [s for s, b in bindings.items() if b["managed"]],
            "unmanaged_stages": [s for s, b in bindings.items() if not b["managed"]],
            # 跨 manifest 声明使用的阶段单独列出来：发起后页面要当场提示，
            # 而不是等出问题再回头猜"当时那个包是不是选错了"。
            "mismatched_stages": [s for s, b in bindings.items() if b.get("stage_mismatch")],
        }

    @classmethod
    def plan_execution(cls, *, project, workflow_id, stage, actor=None) -> dict:
        """「执行本阶段」：**前置门禁校验 + 执行参数下发**，不代替执行本身。

        为什么这个方法不自己去跑阶段：平台只有 ``test_execution`` 有真正的执行器
        （TestExecution + Celery，且必须先由人选好用例套件），另外三个阶段没有可被
        服务端直接调用的生成实现——它们的产出由 agent 经 ``/orchestrator/agent-loop/``
        提交。造一个"点了就在后台跑"的假入口，只会让人以为跑起来了、而实际什么都没发生，
        比没有这个按钮更糟。所以这里做三件**真事**：

        1. **把"能不能执行"收敛到与 ``assert_can_enter`` 同一个判断**——不允许越阶段执行。
           否则步骤条上的"逐阶段推进"只是装饰：第 3 阶段能在第 2 阶段没放行时就点开；
        2. **下发执行参数**：通道、``module_key``、本阶段锁定的 Skill 版本、上一阶段的
           产出 ID（agent 需要它作为上下文）。没有这些参数，执行方只能靠猜，
           产出也归属不到正确的 workflow；
        3. **落痕**：写 ``gate.detail["execution"]``（谁、何时、哪条通道），无门禁时
           占位建一条。步骤条据此如实显示"已派发、等待产出"，而不是点了没反应。

        产出回来时 ``register_output`` 会接管：状态重置为 ``pending``、``detail`` 清空，
        阶段随即亮起。所以这里的留痕是"执行中"的临时证据，不参与放行判定。
        """
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise ValidationError("必须提供 workflow_id")

        # 越阶段执行必须挡住——这是"逐阶段推进"从界面约定变成真实约束的地方。
        cls.assert_can_enter(project.pk, workflow_id, stage)

        # 上游阶段要按**该流程自己的序列**取，否则存量流程会取到新序列里的阶段，
        # 下游 agent 拿到的"上游产出"就是另一个阶段的东西。
        order = cls.stage_order_for(project.pk, workflow_id, stage=stage)
        if stage not in order:
            raise ValidationError(f"阶段 {stage} 不属于流程 {workflow_id}")
        position = order.index(stage)
        previous_stage = order[position - 1] if position else ""
        parent_output_ids: list[str] = []
        if previous_stage:
            previous_gate = WorkflowStageGate.objects.filter(
                project_id=project.pk, workflow_id=workflow_id, stage=previous_stage
            ).first()
            if previous_gate is not None and previous_gate.output_id:
                parent_output_ids.append(str(previous_gate.output_id))

        lock = WorkflowSkillLock.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, lock_key=f"stage:{stage}"
        ).select_related("skill", "skill_version").first()
        channel = dict(cls.STAGE_EXECUTION_CHANNELS.get(stage, {}))

        gate = WorkflowStageGate.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, stage=stage
        ).first()
        requested_at = timezone.now()
        plan = {
            "workflow_id": workflow_id,
            "stage": stage,
            "stage_label": cls.STAGE_LABELS.get(stage, stage),
            "channel": channel.get("channel", ""),
            "module_key": channel.get("module_key", ""),
            "entry": channel.get("entry", ""),
            "hint": channel.get("hint", ""),
            "parent_output_ids": parent_output_ids,
            "managed": lock is not None,
            "skill_name": lock.skill.name if lock is not None and lock.skill_id else "",
            "skill_version": (
                lock.skill_version.version if lock is not None and lock.skill_version_id else ""
            ),
            "package_sha256": lock.package_sha256 if lock is not None else "",
            # 重跑会替换产出；页面需要先提醒，否则"我只是再点一下"会静默覆盖已评过的结果。
            "replaces_output": bool(gate is not None and gate.output_id),
            "requested_by": getattr(actor, "username", "") or "",
            "requested_at": requested_at.isoformat(),
        }

        if gate is None:
            gate = WorkflowStageGate.objects.create(
                project=project, workflow_id=workflow_id, stage=stage, status="pending",
            )
        gate.detail = {**(gate.detail or {}), "execution": {
            "requested_by": plan["requested_by"],
            "requested_at": plan["requested_at"],
            "channel": plan["channel"],
            "module_key": plan["module_key"],
        }}
        gate.save(update_fields=["detail", "updated_at"])
        return plan

    @classmethod
    def workflow_status(cls, *, project, workflow_id) -> dict:
        """四阶段门禁 + 锁定版本的**唯一读取入口**。

        页面与接口必须读同一份数据，否则会出现"页面显示可以继续、接口却 400"
        这种谁都不认账的状态不一致。这里把两件事一次算清：

        - 每一阶段的门禁状态与放行留痕；
        - 每一阶段当时**锁定的**Skill 版本——刻意不读"当前活跃版本"，
          因为链路中途激活新版本之后，读活跃版本会让历史流水线显示成"用了新包"，
          而它实际跑的是旧包。这正是"链路中途激活新版本不改变锁"要守住的东西。
        """
        from .task_binding import TaskSkillBindingService
        from .workflow_models import WorkflowStageGate

        workflow_id = str(workflow_id or "").strip()
        gates = {
            gate.stage: gate
            for gate in WorkflowStageGate.objects.filter(
                project_id=project.pk, workflow_id=workflow_id
            ).select_related("decided_by", "output")
        }
        outputs = {}
        for output in GenerationOutput.objects.filter(project_id=project.pk):
            protocol = (output.metadata or {}).get("protocol") or {}
            if str(protocol.get("workflow_id") or "") != workflow_id:
                continue
            outputs[str(protocol.get("stage") or output.task_type)] = output
        binding = TaskSkillBindingService.binding_for_workflow(
            project=project, workflow_id=workflow_id,
        )
        locks = {item["stage"]: item for item in binding["locks"] if item["stage"]}

        stages = []
        blocked_at = ""
        # 阶段序列按**这条流程自己的**解释：存量流程用历史序列，新流程用新序列。
        # 套用全局序列会让存量流程的 test_plan_generation / report_generation
        # 直接消失，页面上表现为"流程少了两个阶段"。
        stage_order = cls.stage_order_for(project.pk, workflow_id)
        for index, stage in enumerate(stage_order):
            gate = gates.get(stage)
            output = outputs.get(stage)
            lock = locks.get(stage)
            entered = output is not None or gate is not None
            execution = (gate.detail or {}).get("execution") if gate is not None else None
            if not entered:
                # 还没轮到的阶段：只看上一阶段是否已放行。
                previous = (
                    None if index == 0
                    else gates.get(stage_order[index - 1])
                )
                can_enter = index == 0 or bool(
                    previous and previous.status in GATE_PASSING_STATES
                )
                state = "ready" if can_enter else "blocked"
                if not can_enter and not blocked_at:
                    blocked_at = stage
            elif output is None and execution:
                # 已派发、还没产出。这里必须与"待测评"区分开：两者对使用者的含义
                # 完全不同——`running` 是该等（产出由外部 agent / 测试执行推进），
                # `pending` 是该动手（产出已有，等评测算账）。
                can_enter = True
                state = "running"
            else:
                can_enter = True
                state = gate.status if gate is not None else "pending"
            stages.append({
                "stage": stage,
                "label": cls.STAGE_LABELS.get(stage, stage),
                "state": state,
                "entered": entered,
                "can_enter": can_enter,
                "execution": execution,
                "output": ({
                    "id": str(output.pk), "task_id": output.task_id,
                    "created_at": output.created_at.isoformat() if output.created_at else "",
                } if output is not None else None),
                "gate": ({
                    "id": str(gate.pk), "status": gate.status,
                    "scores": gate.scores, "threshold": gate.threshold,
                    "reason": gate.reason,
                    "decided_by": (
                        getattr(gate.decided_by, "username", "") if gate.decided_by_id else ""
                    ),
                    "decided_at": gate.decided_at.isoformat() if gate.decided_at else "",
                    "overridden": gate.status == "overridden",
                    # 页面据此决定"人工确认"/"人工评分"按钮显不显示：与 ``confirm`` /
                    # ``score`` 的准入判断同源，避免按钮显示出来却被接口 400 拒掉。
                    "confirmable": gate.status in GATE_CONFIRMABLE_STATES,
                    "scorable": gate.status in GATE_SCORABLE_STATES,
                    "passed": gate.status in GATE_PASSING_STATES,
                    "human_decided": cls.is_human_decided(gate),
                    "manual_score": (gate.detail or {}).get("manual_score"),
                    # T16：报告契约与阶段/端到端评测结论随门禁一起回给页面，
                    # 状态和证据同源，避免页面自己再算一遍算出第二种结论。
                    "report_contract": (gate.detail or {}).get("report_contract"),
                    "evaluation": (gate.detail or {}).get("evaluation"),
                } if gate is not None else None),
                "version": (lock or None),
            })
        # 收口阶段同样按本流程的序列取：新流程是问题跟踪，存量流程是报告生成。
        closing_stage = stage_order[-1] if stage_order else ""
        report = gates.get(closing_stage)
        report_output = outputs.get(closing_stage)
        report_contract = None
        if report is not None:
            report_contract = (report.detail or {}).get("report_contract")
        if report_contract is None and report_output is not None:
            # 有收口产出但门禁还没建（例如旁路产出）时现算一份，避免页面显示"无契约"。
            from .report_gates import ReportGateService

            report_contract = ReportGateService.validate(report_output)
        return {
            "workflow_id": workflow_id,
            "stage_order": list(stage_order),
            "stages": stages,
            # 当前该看哪一阶段：第一个还没放行的阶段。步骤条只把它前面的做成"已完成"、
            # 把它自己做成"进行中/可执行"，其余留灰——这就是"逐阶段推进"的判据。
            # 由后端算而不是前端算：否则"哪一步算完成"会出现两份实现，
            # 页面显示第 3 步可执行、接口却拒绝进入，又是状态不一致。
            "current_stage": next(
                (item["stage"] for item in stages
                 if item["state"] not in GATE_PASSING_STATES),
                "",
            ),
            "locked_version_count": len(locks),
            "managed": bool(locks),
            # 契约结论随状态一起回给页面；页面据此解释"为什么报告阶段还没过"。
            # 但它**不参与** completed 判定：报告门禁只有走到 passed 才可能通过契约
            # （见 ``evaluate``），而负责人留痕放行（overridden）或人工确认
            # （confirmed）是人的决定，不能让契约反过来否决——这会把"留痕放行"
            # 变成一句空话，也会让"确认后可以直接进入下一步"在最后一阶段失效。
            "report_contract": report_contract,
            "completed": bool(
                report is not None and report.status in GATE_PASSING_STATES
                and report_output is not None
            ),
            "blocked_at": blocked_at,
        }

    # ------------------------------------------------- 内部

    @staticmethod
    def _binding_view(binding: dict) -> dict:
        version = binding.get("skill_version")
        lock = binding.get("lock")
        return {
            "stage": binding.get("stage", ""),
            "managed": bool(binding.get("managed")),
            "skill_id": str(getattr(version, "skill_id", "") or ""),
            "skill_name": (version.skill.name if version is not None and version.skill_id else ""),
            "skill_version_id": str(getattr(version, "pk", "") or ""),
            "version": str(getattr(version, "version", "") or ""),
            "package_sha256": str(getattr(version, "package_sha256", "") or ""),
            "lock_id": str(getattr(lock, "pk", "") or ""),
            "pinned": bool(binding.get("pinned")),
            "declared_stage": str(binding.get("declared_stage") or ""),
            "stage_mismatch": bool(binding.get("stage_mismatch")),
            "detail": binding.get("detail", ""),
        }


class ProjectQualityCockpitService:
    # 只保留「能力能被 Skill 直接迭代升级」的单次能力：代码审查与知识库问答
    # 属平台基础能力，不进 Agent 台账，也不该在独立能力面板里占位。
    # 口径见 ``capability_registry`` 与 ``specs/agent-ledger/requirements.md`` §3。
    #
    # ⚠️ 这是**面板展示范围**这一产品决策，不是口径推导，所以刻意留成字面量：
    # 注册表里 ``MODE_SINGLE`` 的还有 ``risk_identification`` / ``issue_tracking``，
    # 但这两者在平台里没有真实调用方，库里只有演练数据，展示出来只会让面板
    # 看起来比实际热闹。它们哪天接了真实业务流，再加进这里。
    SINGLE_STAGES = ["case_review"]

    def summarize(self, project_id):
        # 质量驾驶舱只呈现测试业务角色，不把项目管理员混入质量团队。
        # 现阶段使用项目 owner 作为测试负责人、member 作为测试执行人；
        # admin 仅保留项目管理权限，不在此处展示。
        memberships = ProjectMember.objects.filter(
            project_id=project_id, role__in=["owner", "member"]
        ).select_related("user")
        leads, executors = [], []
        for membership in memberships:
            user = membership.user
            item = {
                "id": user.id,
                "username": user.username,
                "display_name": (user.get_full_name() or user.username).strip(),
                "email": user.email,
                "project_role": membership.role,
            }
            (leads if membership.role == "owner" else executors).append(item)

        datasets = GoldDataset.objects.filter(project_id=project_id).annotate(
            version_count_value=Count("versions", distinct=True),
            case_count_value=Count("versions__cases", distinct=True),
        ).order_by("task_type", "name")
        gold_by_type = {}
        for dataset in datasets:
            gold_by_type.setdefault(dataset.task_type, []).append({
                "id": str(dataset.id), "name": dataset.name, "status": dataset.status,
                "versions": dataset.version_count_value, "cases": dataset.case_count_value,
            })

        outputs = GenerationOutput.objects.filter(project_id=project_id)
        feedback = FeedbackEvent.objects.filter(project_id=project_id)
        singles = []
        for stage in self.SINGLE_STAGES:
            stage_outputs = outputs.filter(task_type=stage)
            singles.append({
                "stage": stage,
                "outputs": stage_outputs.count(),
                "feedback": feedback.filter(output__task_type=stage).count(),
                "failed": stage_outputs.filter(trace__status="failed").count(),
                "latest_at": stage_outputs.order_by("-created_at").values_list("created_at", flat=True).first(),
                # 能不能自进化由注册表回答，不在前端另判一次：代码审查是复合能力
                # （不打包成 Skill），知识库问答是平台工具，两者 ``is_evolvable``
                # 都是 False，界面据此不显示「发起流程」按钮。
                "self_evolution": is_evolvable(stage),
            })

        workflow_outputs = outputs.filter(
            task_type__in=list(ALL_WORKFLOW_STAGES)
        ).exclude(metadata__protocol__workflow_id="").select_related(
            "capability__active_release"
        ).order_by("created_at")
        workflow_ids = []
        grouped = {}
        #: 每条流程的"时间足迹"：发起（最早锁定/最早产出）与最近动静（最新锁定/最新门禁）。
        #: 左侧流程版本列表要按这个排序并显示"何时发起"——缺了它，用户只能看到一堆
        #: 长得一样的 workflow_id，区分不出哪条是刚建的、哪条是上个月的。
        flow_span = {}

        def _touch_span(workflow_id, moment):
            if moment is None:
                return
            item = flow_span.setdefault(workflow_id, {"first": moment, "last": moment})
            if moment < item["first"]:
                item["first"] = moment
            if moment > item["last"]:
                item["last"] = moment

        for output in workflow_outputs:
            protocol = (output.metadata or {}).get("protocol") or {}
            workflow_id = str(protocol.get("workflow_id") or "")
            if not workflow_id:
                continue
            if workflow_id not in grouped:
                workflow_ids.append(workflow_id)
                grouped[workflow_id] = {}
            grouped[workflow_id][protocol.get("stage") or output.task_type] = output
            _touch_span(workflow_id, output.created_at)
        # 已发起但尚未产出的流程也要进列表：``start_workflow`` 只锁版本、不写产出，
        # 若只从产出反推，用户发起完流程会看到列表依旧是空的，误以为发起失败。
        # 按 ``locked_at`` 升序追加，配合末尾的 ``reversed`` 让最新的流程排在最前。
        for workflow_id, locked_at in (
            WorkflowSkillLock.objects
            .filter(project_id=project_id)
            .exclude(workflow_id="")
            .order_by("locked_at")
            .values_list("workflow_id", "locked_at")
        ):
            if workflow_id not in grouped:
                workflow_ids.append(workflow_id)
                grouped[workflow_id] = {}
            _touch_span(workflow_id, locked_at)
        gates = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id__in=workflow_ids
        ).select_related("decided_by")
        gate_map = {(gate.workflow_id, gate.stage): gate for gate in gates}
        for gate in gates:
            _touch_span(gate.workflow_id, gate.updated_at)
        # T15：阶段用的版本必须读**任务启动时锁定的**那一份。
        # 早先这里是 ``output.capability.active_release.version``，读的是"当前活跃版本"——
        # 链路跑到一半有人激活了新版本后，历史流水线在页面上会显示成用了新包，
        # 而它实际跑的是旧包。这既是显示错误，也让回滚无法界定影响范围。
        lock_map = {}
        for lock in WorkflowSkillLock.objects.filter(
            project_id=project_id, workflow_id__in=workflow_ids
        ).select_related("skill", "skill_version"):
            if lock.stage:
                lock_map[(lock.workflow_id, lock.stage)] = lock
            if lock.skill_version_id:
                # 兜底索引：老数据可能没有 stage 字段，但产出自己记了版本。
                lock_map.setdefault(("__version__", lock.skill_version_id), lock)
        workflows = []
        for workflow_id in reversed(workflow_ids[-20:]):
            previous_open = True
            current_stage = ""
            stages = []
            # 控制台与 ``workflow-status`` 必须给出**同一套**阶段序列，
            # 否则列表页与详情页的阶段数会对不上（存量流程尤其明显）。
            flow_stage_order = WorkflowGateService.stage_order_for(project_id, workflow_id)
            for stage in flow_stage_order:
                output = grouped[workflow_id].get(stage)
                gate = gate_map.get((workflow_id, stage))
                if output and not gate:
                    gate = WorkflowGateService.register_output(output)
                execution = (gate.detail or {}).get("execution") if gate is not None else None
                if output:
                    status = gate.status if gate else "pending"
                elif execution:
                    # 已派发、等待产出：与"可执行/已阻断"区分开，步骤条才有"执行中"这一档。
                    status = "running"
                else:
                    status = "ready" if previous_open else "blocked"
                if status not in GATE_PASSING_STATES and not current_stage:
                    current_stage = stage
                lock = lock_map.get((workflow_id, stage))
                if lock is None and output is not None and output.skill_version_id:
                    lock = lock_map.get(("__version__", output.skill_version_id))
                skill_version = ""
                skill_name = ""
                if lock is not None:
                    skill_name = lock.skill.name if lock.skill_id else ""
                    skill_version = lock.skill_version.version if lock.skill_version_id else ""
                elif output is not None:
                    skill_name = output.capability.name if output.capability_id else ""
                stages.append({
                    "stage": stage, "status": status,
                    "output_id": str(output.id) if output else None,
                    "task_id": output.task_id if output else "",
                    "gate_id": str(gate.id) if gate else None,
                    "scores": gate.scores if gate else {},
                    "threshold": gate.threshold if gate else 0.7,
                    "reason": gate.reason if gate else "",
                    "decided_by": gate.decided_by.username if gate and gate.decided_by else "",
                    # 与 ``workflow-status`` 同源：控制台也要能显示「人工确认」按钮，
                    # 且显隐判断必须和 ``confirm`` / ``score`` 的准入集合一致。
                    "confirmable": bool(gate and gate.status in GATE_CONFIRMABLE_STATES),
                    "scorable": bool(gate and gate.status in GATE_SCORABLE_STATES),
                    "passed": bool(gate and gate.status in GATE_PASSING_STATES),
                    "human_decided": bool(gate and WorkflowGateService.is_human_decided(gate)),
                    "manual_score": (gate.detail or {}).get("manual_score") if gate else None,
                    "execution": execution,
                    "skill_name": skill_name,
                    "skill_version": skill_version,
                    "version_locked": lock is not None,
                    "package_sha256": lock.package_sha256 if lock is not None else "",
                    "self_evolution": lock is not None or bool(output and output.capability_id),
                })
                previous_open = bool(
                    output and gate and gate.status in GATE_PASSING_STATES
                )
            workflows.append({
                "workflow_id": workflow_id,
                # 与 ``workflow-status`` 同源的"当前该看哪一步"，供步骤条高亮。
                "current_stage": current_stage,
                "stages": stages,
                # 左侧流程版本列表需要的元信息。放在后端算而不是前端从 stages 推：
                # "发起时间"根本不在 stages 里，前端推不出来；而"锁了几个阶段"
                # 前端推得出、却会和门禁口径各写一遍。
                "stage_order": list(flow_stage_order),
                "stage_template": (
                    "current" if list(flow_stage_order) == list(DEFAULT_WORKFLOW_STAGE_ORDER)
                    else "legacy"
                ),
                "locked_version_count": sum(1 for s in stages if s["version_locked"]),
                "passed_count": sum(1 for s in stages if s["passed"]),
                "completed": bool(stages) and all(s["passed"] for s in stages),
                "created_at": (flow_span.get(workflow_id) or {}).get("first"),
                "updated_at": (flow_span.get(workflow_id) or {}).get("last"),
            })
        return {
            "people": {"leads": leads, "executors": executors},
            "gold_by_type": gold_by_type,
            "single_capabilities": singles,
            "workflows": workflows,
            # 新流程的默认序列；页面据此渲染"还没发起前"的四个阶段占位。
            "stage_order": list(DEFAULT_WORKFLOW_STAGE_ORDER),
        }


class KnowledgeHealthService:
    def inspect(self, project_id):
        expired = KnowledgeVersion.objects.filter(
            asset__project_id=project_id, status="published", expires_at__lt=timezone.now()
        )
        failed_projections = IndexProjection.objects.filter(project_id=project_id, state="failed")
        stale_projections = IndexProjection.objects.filter(project_id=project_id, state="stale")
        open_conflicts = KnowledgeConflict.objects.filter(project_id=project_id, state__in=["open", "resolving"])
        isolated_nodes = GraphNode.objects.filter(project_id=project_id).annotate(
            degree=Count("outgoing_edges") + Count("incoming_edges")
        ).filter(degree=0)
        return {
            "project_id": project_id,
            "checked_at": timezone.now().isoformat(),
            "expired_versions": expired.count(),
            "failed_projections": failed_projections.count(),
            "stale_projections": stale_projections.count(),
            "open_conflicts": open_conflicts.count(),
            "isolated_graph_nodes": isolated_nodes.count(),
            "healthy": not any((expired.exists(), failed_projections.exists(), open_conflicts.exists())),
        }


class WorkflowGraphBuilder:
    """从统一协议 metadata 构建需求/风险—方案—用例—执行—问题联合图。"""
    def __init__(self, graph=None):
        self.graph = graph or PostgreSQLGraphSource()

    def build(self, project_id, workflow_id):
        outputs = list(GenerationOutput.objects.filter(
            project_id=project_id, metadata__protocol__workflow_id=workflow_id
        ).order_by("created_at"))
        nodes, edges = [], []
        for output in outputs:
            protocol = (output.metadata or {}).get("protocol") or {}
            external_id = f"output:{output.id}"
            nodes.append(GraphNodeSpec(
                node_type="workflow_output", external_id=external_id,
                name=f"{protocol.get('stage', output.task_type)}:{output.task_id}",
                properties={"workflow_id": workflow_id, "stage": protocol.get("stage"), "output_id": str(output.id)},
            ))
            for parent_id in protocol.get("parent_output_ids") or []:
                edges.append(GraphEdgeSpec(
                    from_external_id=f"output:{parent_id}", to_external_id=external_id,
                    relation="FEEDS_INTO", properties={"workflow_id": workflow_id},
                ))
        self.graph.upsert_nodes(project_id=project_id, projection_id=None, nodes=nodes)
        edge_count = self.graph.add_edges(project_id=project_id, projection_id=None, edges=edges)
        return {"workflow_id": workflow_id, "node_count": len(nodes), "edge_count": edge_count}


class FlywheelMetricsService:
    def summarize(self, project_id):
        traces = RetrievalTrace.objects.filter(project_id=project_id)
        feedback = FeedbackEvent.objects.filter(project_id=project_id)
        results = EvaluationResult.objects.filter(run__suite__project_id=project_id, status="completed")
        signals = dict(feedback.values_list("signal").annotate(count=Count("id")))
        accepted = signals.get("accepted", 0) + signals.get("defect_confirmed", 0) + signals.get("test_passed", 0)
        rejected = signals.get("rejected", 0) + signals.get("false_positive", 0)
        decisions = accepted + rejected
        return {
            "project_id": project_id,
            "traces": traces.count(),
            "outputs": GenerationOutput.objects.filter(project_id=project_id).count(),
            "feedback": feedback.count(),
            "signals": signals,
            "adoption_rate": accepted / decisions if decisions else None,
            "false_positive_rate": signals.get("false_positive", 0) / decisions if decisions else None,
            "miss_count": signals.get("missed", 0),
            "token_usage": traces.aggregate(total=Sum("token_usage"))["total"] or 0,
            "average_l0": results.aggregate(value=Avg("l0_score"))["value"],
            "average_l1": results.aggregate(value=Avg("l1_score"))["value"],
            "average_l2": results.aggregate(value=Avg("l2_score"))["value"],
            "average_l3": results.aggregate(value=Avg("l3_score"))["value"],
            "evaluation_runs": EvaluationRun.objects.filter(suite__project_id=project_id).count(),
            "active_releases": CapabilityRelease.objects.filter(project_id=project_id, state="active").count(),
            "rollbacks": CapabilityRelease.objects.filter(project_id=project_id, state="rolled_back").count(),
            "open_conflicts": KnowledgeConflict.objects.filter(project_id=project_id, state__in=["open", "resolving"]).count(),
        }
