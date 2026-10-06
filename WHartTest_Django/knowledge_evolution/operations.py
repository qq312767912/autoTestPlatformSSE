"""任务 17–19：防腐检查、联合链路图和运营指标。"""
import logging
import os
import re
import uuid
from collections import Counter
from datetime import timedelta

from django.db.models import Avg, Count, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from projects.models import ProjectMember

from .capability_models import CapabilityRelease
from .capability_registry import (
    ALL_WORKFLOW_STAGES,
    LEGACY_WORKFLOW_STAGES,
    WORKFLOW_STAGES,
    is_evolvable,
    stage_display_label,
)
from .evaluation_models import EvaluationResult, EvaluationRun
from .graph import GraphEdgeSpec, GraphNodeSpec, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import IndexProjection, KnowledgeConflict, KnowledgeVersion
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .gold_models import GoldDataset
from .workflow_models import (
    ATTEMPT_ACTIVE_STATES,
    ATTEMPT_TRANSITIONS,
    EXECUTION_CONTEXT_TTL_MINUTES,
    GATE_CONFIRMABLE_STATES,
    GATE_HUMAN_FINAL_STATES,
    GATE_PASSING_STATES,
    GATE_SCORABLE_STATES,
    StageExecutionContext,
    StageExecutionAttempt,
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
        2. 公开 Skill Hub 中的**全部版本**，带上版本号与包哈希；声明了该阶段的排在
           前面。前端在本地按关键词过滤，项目选择具体版本后写入自己的流程锁。

        刻意**不按 manifest 声明硬筛候选**：主链路刚把阶段换成风险识别/问题跟踪，
        现存包声明的还是旧阶段，硬筛会让向导在这两个阶段一个候选都给不出来——
        而"人选了它"本来就是比"包里写了什么"更强的事实（见 ``bind_stage``）。

        包不可运行（没有 active 版本）时仍然列出来，标 ``runnable=False``：
        让人看见"这个包在、但还不能用"，比它凭空消失更好排查。
        """
        from skills.models import SkillVersion

        targets = list(stages or DEFAULT_WORKFLOW_STAGE_ORDER)
        unknown = [item for item in targets if item not in ALL_WORKFLOW_STAGE_SET]
        if unknown:
            raise ValidationError(f"未知的阶段：{'、'.join(unknown)}")

        # 声明阶段要**连候选版本一起看**：一个包可能已经声明了阶段、
        # 只是还没激活版本。此时它的声明仍然是有用信息（"这个包本就打算干这件事"），
        # 只读活跃版本会让向导把它算成"没有包声明本阶段"，于是默认项空着。
        skills: list[dict] = []
        for version in (
            SkillVersion.objects
            .select_related("skill", "release")
            .order_by("skill__name", "-created_at")
        ):
            skill = version.skill
            manifest = version.manifest or {}
            release = version.release
            declared = str(manifest.get("stage") or "") or str(skill.declared_stage or "")
            runnable = bool(version.is_runnable)
            skills.append({
                "skill_id": str(skill.pk),
                "skill_name": skill.name,
                "description": (skill.description or "").strip(),
                "declared_stage": declared,
                # 展示名统一走能力注册表的 :func:`stage_display_label`：它既认得本类那份
                # 精简标签表之外的全部规范阶段（case_review / platform_base 等），
                # 也会把用户自定义阶段的 ``custom:`` 前缀剥掉。本类自己的 ``STAGE_LABELS``
                # 是 agent 侧的阶段命名，只覆盖主链路，不能拿来当 Skill 归属的展示真值。
                "declared_stage_label": stage_display_label(declared),
                "runnable": runnable,
                "skill_version_id": str(version.pk),
                "version": str(version.version),
                "release_state": release.state if release is not None else "",
                "package_sha256": str(version.package_sha256),
                "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
            })

        stages_payload = []
        for stage in targets:
            declared = [item for item in skills if item["declared_stage"] == stage]
            stages_payload.append({
                "stage": stage,
                "label": cls.STAGE_LABELS.get(stage, stage),
                "default": next((item for item in declared if item["runnable"]), None),
                "declared_skill_ids": list(dict.fromkeys(item["skill_id"] for item in declared)),
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

    # ---- 阶段产出的出口：查看 / 下载 / 反馈共用同一套定位与物化 ----
    #
    # 这三个动作必须落在**同一条产出**上，否则会出现"页面能看结果、却下不到报告"
    # 这种自相矛盾的状态。所以定位只实现一次，三个入口都调它。

    #: 产出侧登记报告文件的约定 key。改名等于换协议，Skill 侧与平台须同时改。
    ARTIFACT_KEY = "report"

    #: 无登记产物时回落渲染的模板后缀。刻意是 markdown 而非 xlsx：
    #: 平台只认识产出正文与协议元数据，硬凑一份 xlsx 会得到"字段全空的表格"，
    #: 比直接给文本更误导。详见 ``render_stage_report``。
    FALLBACK_SUFFIX = ".md"

    @classmethod
    def locate_stage_output(cls, *, project_id, workflow_id: str, stage: str):
        """三元定位某阶段的产出，返回 ``(output, gate)``；找不到时 output 为 ``None``。

        定位刻意走 **project + workflow_id + stage 三者一起**，而不是按产出 id 取：
        产出 id 会出现在页面 URL 与导出的报告里，只按 id 取会让越权读取退化成
        "取决于 id 是否被猜中"。这里多一次三元定位，
        换来的是越权读取在这个入口上不可能发生。

        与 ``workflow-stage-output`` 视图的历史行为保持一致：先看门禁上挂的产出，
        找不到再按协议元数据旁路匹配（有产出但门禁还没建的情况）。
        """
        from .models import GenerationOutput
        from .workflow_models import WorkflowStageGate

        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=stage
        ).select_related("output", "output__skill_version").first()
        output = gate.output if gate is not None and gate.output_id else None
        if output is not None:
            return output, gate

        # 兜底：旁路产出（有产出但门禁还没建）也要能定位，与 workflow-status 的兜底一致。
        for candidate in GenerationOutput.objects.filter(
            project_id=project_id
        ).select_related("skill_version"):
            protocol = (candidate.metadata or {}).get("protocol") or {}
            if (
                str(protocol.get("workflow_id") or "") == workflow_id
                and str(protocol.get("stage") or candidate.task_type) == stage
            ):
                return candidate, gate
        return None, gate

    @staticmethod
    def _resolve_artifact_path(raw) -> str | None:
        """把登记的产物路径解析成真实存在的文件路径；找不到返回 ``None``。

        允许三种写法：绝对路径、相对 ``BASE_DIR``（设计约定写 ``media/...``）、
        相对 ``MEDIA_ROOT``（Django ``FileField`` 的存法）。
        三种都试不是宽容，而是这三处都可能出现在真实产出里；
        只认一种会让"登记了却下不到"变成一个查不出原因的现象。

        **文件不存在等价于没有产物**（返回 ``None``），由调用方回落到模板：
        先给出按钮再报 500 是把"没登记"说成"平台坏了"，与 T23 ``_url`` 同源。
        """
        from django.conf import settings

        text = str(raw or "").strip()
        if not text:
            return None
        candidates = []
        if os.path.isabs(text):
            candidates.append(text)
        else:
            candidates.append(os.path.join(str(settings.BASE_DIR), text))
            media_root = str(getattr(settings, "MEDIA_ROOT", "") or "")
            if media_root:
                candidates.append(os.path.join(media_root, text))
                candidates.append(os.path.join(media_root, os.path.basename(text)))
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    @classmethod
    def stage_artifact(cls, output, *, key: str = "") -> dict | None:
        """取该产出登记的报告文件（``metadata.artifacts``）；没有则返回 ``None``。

        约定结构（见 ``specs/evolution-assisted-loop/design.md`` §1.1）::

            [{ "key": "report", "name": "回归-20261003-方案.xlsx",
               "path": "media/skill_runtime/artifacts/...", "sha256": "...", "size": 15360 }]

        只返回**文件确实存在**的那一条。返回 ``source="registered"`` 让调用方
        能区分"下发的是 Skill 的真实产物"还是"平台兜底渲染的文本"——
        这两者对使用者意味着不同的可信度，不该在响应里长得一样。
        """
        artifacts = (output.metadata or {}).get("artifacts") or []
        if not isinstance(artifacts, list):
            return None
        wanted = str(key or cls.ARTIFACT_KEY)
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            if str(item.get("key") or cls.ARTIFACT_KEY) != wanted:
                continue
            path = cls._resolve_artifact_path(item.get("path"))
            if path is None:
                continue
            size = item.get("size")
            if not isinstance(size, int) or size <= 0:
                try:
                    size = os.path.getsize(path)
                except OSError:  # pragma: no cover - 竞态：解析后被删
                    size = 0
            return {
                "key": wanted,
                "name": str(item.get("name") or os.path.basename(path)),
                "path": path,
                "sha256": str(item.get("sha256") or ""),
                "size": size,
                "source": "registered",
            }
        return None

    @classmethod
    def _fallback_filename(cls, *, output, stage: str) -> str:
        """回落模板的文件名。与 ``render_stage_report`` 共用一个实现：

        名字只算一次，是为了让"列表里预告的名字"与"实际下载到的名字"必然一致 ——
        各算一次迟早会因为 sanitize 规则改动而分叉。
        """
        label = cls.STAGE_LABELS.get(stage, stage)
        safe_label = re.sub(r'[/\\:*?"<>|\r\n]+', "_", label).strip(" ._") or "stage"
        stamp = output.created_at.strftime("%Y%m%d") if output.created_at else "undated"
        return f"{safe_label}阶段报告_{stamp}{cls.FALLBACK_SUFFIX}"

    @classmethod
    def render_stage_report(cls, *, output, stage: str) -> tuple[str, bytes]:
        """按 ``content`` 渲染统一模板，作为「没有登记产物」时的回落。

        返回 ``(文件名, 文件字节)``。

        刻意只做文本包装、不臆造字段。平台能拿到的只有产出正文与协议元数据；
        把它渲染成 xlsx 会得到一份"看起来像报告、其实字段全空"的文件 ——
        相比直接给 markdown，后者至少诚实。
        """
        label = cls.STAGE_LABELS.get(stage, stage)
        protocol = (output.metadata or {}).get("protocol") or {}
        created_at = output.created_at
        skill_version = ""
        if output.skill_version_id:
            try:
                skill_version = str(output.skill_version.version or "")
            except Exception:  # pragma: no cover - 版本被清理
                skill_version = ""
        lines = [
            f"# {label}阶段报告",
            "",
            f"- 流程：{protocol.get('workflow_id') or '-'}",
            f"- 阶段：{label}（{stage}）",
            f"- 产出时间：{created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else '-'}",
            f"- Skill 版本：{skill_version or '-'}",
            f"- 包哈希：{output.skill_package_sha256 or '-'}",
            "",
            "> 本文件由平台按阶段产出正文回落生成：该阶段没有登记可下载的产物文件。",
            "> 如需正式报告格式，请由该阶段的 Skill 在产出时登记 artifacts。",
            "",
            "---",
            "",
            (output.content or "（本阶段产出正文为空）"),
            "",
        ]
        # BOM 让 Excel / WPS 打开中文不乱码：报告会被人工下载后在办公套件里看。
        body = ("\ufeff" + "\n".join(lines)).encode("utf-8")
        return cls._fallback_filename(output=output, stage=stage), body

    @classmethod
    def stage_artifact_payload(cls, output, *, stage: str, key: str = "") -> dict:
        """把「该阶段能下载什么」拍成一份给页面看的描述（**不含文件字节**）。

        给 ``workflow-status`` 用：页面据此决定要不要渲染「下载报告」按钮、
        以及悬浮时提示"下的是登记产物还是平台回落模板"。
        这两者对使用者意味着不同的可信度，不该在按钮上长得一样。

        刻意**不渲染回落正文**来算体积：那会在每次读流程状态时把每个阶段的
        全文拼一遍（内容可能上百 KB），只为填一个前端未必展示的数字。
        回落时 ``size=None``，需要精确体积的调用方看 ``content_length``。
        """
        artifact = cls.stage_artifact(output, key=key)
        if artifact is not None:
            return {
                "available": True,
                "source": "registered",
                "name": artifact["name"],
                "size": artifact["size"],
                "sha256": artifact["sha256"],
            }
        return {
            # 落到这里总是"能下"：回落模板一定会生成。``available`` 因此恒为 True，
            # 保留这个字段是为了让将来"某阶段禁止下载"时前端不用改结构。
            "available": True,
            "source": "fallback",
            "name": cls._fallback_filename(output=output, stage=stage),
            "size": None,
            "sha256": "",
        }

    @classmethod
    def register_output(cls, output, *, create_gate: bool = True):
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id")
        stage = protocol.get("stage") or output.task_type
        if not workflow_id or stage not in ALL_WORKFLOW_STAGE_SET:
            return None
        # 正式阶段产出是候选沉淀的第一类来源（T04 / P5）。入队幂等，重复登记同一产出
        # 不会重复建候选；这里**只入队**，预检与建候选在统一处理器里做，
        # 门禁登记本身不承担任何金标写入副作用。
        try:
            from .gold import AssetCandidateService

            AssetCandidateService.enqueue_from_output(output)
        except Exception:  # noqa: BLE001
            logger.exception("正式阶段产出入候选队列失败，不影响门禁登记")
        # 普通入口的产出只进入待人工审核候选，不创建或改变流程门禁。
        # 显式 workflow 入口才允许继续操作阶段锁与门禁状态。
        if not create_gate:
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
    #:
    #: ``launch_path``（T02）是**业务页面的路由路径**，用来生成 ``launch_url``。
    #: 只有真正有对应业务页面的阶段才填：填一个还不存在的路由，用户点「跳转到 Agent 执行」
    #: 就会落到 404 上——"跳过去了但打不开"比"根本没跳"更让人困惑。
    #: 一期只有方案分析页（``/test-plans``）；其余阶段留空，前端据 ``launch_url``
    #: 是否为空决定是跳转还是沿用原来的"请到某某页面执行"提示。
    STAGE_EXECUTION_CHANNELS = {
        "risk_identification": {
            "channel": "agent",
            "module_key": "risk_identification",
            "entry": "LLM对话",
            "launch_path": "",
            "hint": "由 agent 基于需求/规格说明识别高风险点，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "issue_tracking": {
            "channel": "agent",
            "module_key": "issue_tracking",
            "entry": "LLM对话",
            "launch_path": "",
            "hint": "由 agent 汇总问题清单与闭环状态，产出按统一协议回写本流程后本阶段自动亮起",
        },
        # ---- 以下两个通道只为**存量流程**保留：它们已不在新主链表里，
        # 但历史流程仍可能停在这两个阶段，页面上的「执行本阶段」不能因为
        # 一次口径变更就变成"没有通道"。
        "test_plan_generation": {
            "channel": "agent",
            "module_key": "test_plan_generation",
            "entry": "方案分析",
            "launch_path": "/test-plans",
            "hint": "由 agent 产出测试方案，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "report_generation": {
            "channel": "agent",
            "module_key": "report_generation",
            "entry": "LLM对话",
            "launch_path": "",
            "hint": "由 agent 产出测试报告，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "testcase_generation": {
            "channel": "agent",
            "module_key": "testcase_generation",
            "entry": "LLM对话",
            "launch_path": "",
            "hint": "由 agent 产出测试用例，产出按统一协议回写本流程后本阶段自动亮起",
        },
        "test_execution": {
            "channel": "platform",
            "entry": "测试执行",
            "launch_path": "",
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
            pins: ``{stage: skill_version_id}``——发起流程向导里人**逐阶段选定**的版本。
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
            pinned_ref = requested_pins.get(stage)
            # 新协议传 SkillVersion ID；兼容存量调用方传 Skill ID。新前端只发送版本 ID，
            # 因而不会再由运行时替用户猜活跃/最新版本。
            pinned_version = None
            pinned_skill = None
            if pinned_ref:
                from skills.models import SkillVersion

                try:
                    version_id = uuid.UUID(str(pinned_ref))
                except (ValueError, TypeError, AttributeError):
                    version_id = None
                if version_id is not None:
                    pinned_version = (
                        SkillVersion.objects.filter(pk=version_id).first() or version_id
                    )
                else:
                    pinned_skill = pinned_ref
            binding = TaskSkillBindingService.bind_stage(
                project=project, workflow_id=workflow_id, stage=stage,
                actor=actor, allow_unmanaged=allow_unmanaged,
                skill=pinned_skill, skill_version=pinned_version,
                allow_stage_mismatch=bool(pinned_ref),
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

        # T02：把"派发"从一句参数回执，变成**一条可追踪的执行尝试 + 一份短期上下文**。
        #
        # 幂等刻意用"复用活跃 attempt"而不是幂等键：幂等键要求前端每次派发都算出同一个键，
        # 而页面刷新一次、参数变一点，键就变了——那等于没有幂等。用"这一阶段是不是
        # 已经有一条还没结束的 attempt"来判断，重复点击才真的收敛到同一条上。
        # 想重跑的人有 ``retry`` 这条显式路径，不需要靠连点两下来达成。
        attempt = StageExecutionAttempt.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, stage=stage,
            status__in=sorted(ATTEMPT_ACTIVE_STATES),
        ).order_by("-created_at").first()
        if attempt is None:
            attempt, _created = StageExecutionAttemptService.dispatch(
                project=project, workflow_id=workflow_id, stage=stage, actor=actor,
                skill_version=(lock.skill_version if lock is not None else None),
                parent_output_ids=parent_output_ids,
                entry_type="flywheel",
                detail={
                    "channel": channel.get("channel", ""),
                    "module_key": channel.get("module_key", ""),
                    "entry": channel.get("entry", ""),
                    "hint": channel.get("hint", ""),
                    "plan_requested_at": requested_at.isoformat(),
                    "replaces_output": bool(gate is not None and gate.output_id),
                },
            )

        context = StageExecutionContextService.issue(
            attempt=attempt, project=project, actor=actor,
            channel=channel.get("channel", ""),
            module_key=channel.get("module_key", ""),
            skill_version=(lock.skill_version if lock is not None else None),
            parent_output_ids=parent_output_ids,
            payload={"workflow_stage_order": list(order)},
        )
        launch_path = str(channel.get("launch_path") or "")

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
            # --- T02 新增：业务页面自己不再拼 workflow_id / module_key / SkillVersion ---
            "attempt_id": str(attempt.pk),
            "attempt_status": attempt.status,
            "execution_context_id": str(context.pk),
            "execution_context_expires_at": context.expires_at.isoformat(),
            # 为空表示这一阶段还没有对应业务页面（一期只有方案分析）。
            # 前端据此决定是"直接跳"还是"沿用原来的请到某某页面执行"提示——
            # 空值而不是用一个猜出来的路由，避免跳到 404。
            "launch_url": (
                f"{launch_path}?execution_context_id={context.pk}" if launch_path else ""
            ),
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
                    # 「下载报告」按钮的显示条件与悬浮提示都读这里：
                    # 未产出时不存在这个字段，页面就不渲染按钮（不显示优于禁用 ——
                    # 一个禁用按钮只会让人反复点它想知道为什么）。
                    "artifact": cls.stage_artifact_payload(output, stage=stage),
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


class StageExecutionAttemptService:
    """阶段执行尝试的生命周期服务（T01 / R4）。

    为什么必须有这一层：attempt 的状态机、项目隔离和幂等是三条**跨入口**的约束
    （飞轮派发、Agent 回调、重试按钮、补偿任务都会写 attempt）。如果允许各调用方
    直接 ``attempt.status = "completed"`` 再 ``save()``，那么"非法回退"和
    "跨终态更新"就只是口头约定——而它们的后果是飞轮上出现一条状态自相矛盾的记录，
    事后无法判断这一轮到底跑没跑完。

    所以收口规则只有一条：**所有 attempt 的创建与流转都走这个类**。
    """

    #: ``detail`` 只允许这些键落地。Agent 的原始请求整包塞进来会带上凭据、
    #: 绝对路径和大段参数，而 attempt 是会被飞轮页面读出来展示的。
    DETAIL_KEYS = frozenset({
        "channel", "module_key", "entry", "hint", "workflow_stage_order",
        "plan_requested_at", "replaces_output",
    })

    # ------------------------------------------------------------------ 项目隔离

    @classmethod
    def _assert_same_project(
        cls, *, project, workflow_id, stage, flywheel_run=None,
        skill_version=None, parent_output_ids=None,
    ) -> None:
        """校验流程、父产出与版本锁都属于同一个项目。

        跨项目引用是**静默错误**：接口能返回 201，页面也能显示，只是这条 attempt
        永远归不到正确的流程上，并且会把另一个项目的产出 ID 泄漏给当前项目。
        因此宁可在这里拒绝，也不靠"调用方应该传对"。
        """
        if flywheel_run is not None:
            if flywheel_run.project_id != project.pk:
                raise ValidationError("飞轮流程与当前项目不一致")
            if str(flywheel_run.workflow_id) != str(workflow_id):
                raise ValidationError("飞轮流程与 workflow_id 不一致")

        parent_ids = [str(item) for item in (parent_output_ids or []) if item]
        if parent_ids:
            found = set(
                str(pk) for pk in GenerationOutput.objects
                .filter(pk__in=parent_ids, project_id=project.pk)
                .values_list("pk", flat=True)
            )
            missing = [item for item in parent_ids if item not in found]
            if missing:
                # 不存在与"存在但属于别的项目"合并成同一个结论：
                # 区分开来等于告诉调用方"这个 ID 在别的项目里是真的"。
                raise ValidationError(f"上游产出不属于当前项目或不存在：{missing}")

        lock = WorkflowSkillLock.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, lock_key=f"stage:{stage}",
        ).first()
        if lock is not None and skill_version is not None:
            if lock.skill_version_id and str(lock.skill_version_id) != str(skill_version.pk):
                raise ValidationError("所选 Skill 版本与该流程已锁定版本不一致")
        return lock

    # ------------------------------------------------------------------ 创建

    @classmethod
    def _create(
        cls, *, project, workflow_id, stage, status, skill_version=None,
        parent_output_ids=None, entry_type="flywheel", session_id="",
        idempotency_key="", actor=None, detail=None, flywheel_run=None, retry_of=None,
    ):
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise ValidationError("必须提供 workflow_id")
        if stage not in ALL_WORKFLOW_STAGE_SET:
            raise ValidationError(f"未知的阶段：{stage}")

        key = str(idempotency_key or "")
        if key:
            existing = cls._find_by_idempotency(project=project, idempotency_key=key)
            if existing is not None:
                return existing, False

        lock = cls._assert_same_project(
            project=project, workflow_id=workflow_id, stage=stage,
            flywheel_run=flywheel_run, skill_version=skill_version,
            parent_output_ids=parent_output_ids,
        )

        safe_detail = {
            name: value for name, value in (detail or {}).items()
            if name in cls.DETAIL_KEYS
        }
        now = timezone.now() if status == "dispatched" else None
        attempt = StageExecutionAttempt.objects.create(
            project=project,
            flywheel_run=flywheel_run,
            workflow_id=workflow_id,
            stage=stage,
            skill_version=skill_version,
            skill_package_sha256=(
                str(getattr(skill_version, "package_sha256", "") or "")
                or (lock.package_sha256 if lock is not None else "")
            ),
            parent_output_ids=[str(item) for item in (parent_output_ids or []) if item],
            session_id=str(session_id or ""),
            entry_type=entry_type,
            status=status,
            retry_of=retry_of,
            idempotency_key=key,
            detail=safe_detail,
            requested_by=actor if getattr(actor, "pk", None) else None,
            dispatched_at=now,
        )
        return attempt, True

    @classmethod
    def _find_by_idempotency(cls, *, project, idempotency_key):
        return StageExecutionAttempt.objects.filter(
            project_id=project.pk, idempotency_key=str(idempotency_key),
        ).first()

    @classmethod
    def plan(cls, *, project, workflow_id, stage, **kwargs):
        """只登记"打算执行"，不派发。

        存在的意义是让"用户点了但参数还没准备好"这种状态有地方落：
        Agent 真正起来之前就能查到这一轮，不必等到有产出。
        """
        return cls._create(project=project, workflow_id=workflow_id, stage=stage,
                           status="planned", **kwargs)

    @classmethod
    def dispatch(cls, *, project, workflow_id, stage, attempt=None, **kwargs):
        """派发一轮执行，返回 ``(attempt, created)``。

        幂等在这里生效：同一个 ``idempotency_key`` 重复调用只返回同一条记录，
        ``created=False``。这样"用户连点两下执行"不会变成两条并行的 attempt。

        传入 ``attempt`` 时表示把一条已存在的 ``planned`` 记录推进到 ``dispatched``，
        而不是新建。
        """
        if attempt is not None:
            return cls.transition(attempt, "dispatched", actor=kwargs.get("actor")), False

        created_attempt, created = cls._create(
            project=project, workflow_id=workflow_id, stage=stage,
            status="dispatched", **kwargs,
        )
        if not created:
            return created_attempt, False

        # 版本锁只在派发时才写入 detail：plan 阶段可能还没有锁。
        lock = WorkflowSkillLock.objects.filter(
            project_id=project.pk, workflow_id=workflow_id, lock_key=f"stage:{stage}",
        ).first()
        if lock is not None:
            created_attempt.detail = {
                **(created_attempt.detail or {}),
                "locked_skill_version_id": str(lock.skill_version_id or ""),
            }
        created_attempt.detail = {
            **(created_attempt.detail or {}),
            "dispatched_by": getattr(kwargs.get("actor"), "username", "") or "",
        }
        created_attempt.save(update_fields=["detail", "updated_at"])
        return created_attempt, True

    # ------------------------------------------------------------------ 流转

    #: 每个目标状态要补写的时间戳。集中成一张表，避免每个包装方法各写一遍
    #: ——漏一个就会出现"completed 了但没有 finished_at"这种半截记录。
    _TIMESTAMP_FIELD = {
        "dispatched": "dispatched_at",
        "running": "started_at",
        "output_published": "output_published_at",
        "completed": "finished_at",
        "failed": "finished_at",
        "cancelled": "finished_at",
        "timed_out": "finished_at",
    }

    @classmethod
    def transition(cls, attempt, target, *, actor=None, error_code="",
                   error_summary="", output=None, detail=None):
        """状态迁移的唯一入口。非法回退与跨终态更新都在这里被拒。

        特例：``target`` 与当前状态相同按幂等处理，直接返回不报错。理由是
        SSE 重连、回调重放都会重复上报同一个状态，把重放当成错误会让补偿任务
        永远无法收敛——而"状态没变"本身确实不是问题。
        """
        target = str(target or "")
        if target not in ATTEMPT_TRANSITIONS:
            raise ValidationError(f"未知的执行状态：{target}")
        if target == attempt.status:
            return attempt
        if attempt.is_terminal:
            raise ValidationError(
                f"执行尝试已处于终态 {attempt.status}，不允许再改为 {target}"
            )
        if not attempt.can_transition_to(target):
            raise ValidationError(f"不允许的状态迁移：{attempt.status} → {target}")

        update_fields = ["status", "updated_at"]
        attempt.status = target
        stamp_field = cls._TIMESTAMP_FIELD.get(target)
        if stamp_field:
            setattr(attempt, stamp_field, timezone.now())
            update_fields.append(stamp_field)
        if target == "running" and not attempt.session_id:
            # 没有 session_id 的运行没法关联到对话与轨迹，属调用方失误；
            # 但不该在这里硬拦，交由上层决定。
            logger.info("attempt %s 进入 running 但缺少 session_id", attempt.pk)
        if output is not None:
            attempt.output = output
            update_fields.append("output")
        if error_code:
            attempt.error_code = str(error_code)[:100]
            update_fields.append("error_code")
        if error_summary:
            attempt.error_summary = str(error_summary)
            update_fields.append("error_summary")
        if detail:
            attempt.detail = {**(attempt.detail or {}), **detail}
            update_fields.append("detail")
        attempt.save(update_fields=update_fields)
        return attempt

    @classmethod
    def mark_running(cls, attempt, *, session_id="", actor=None, detail=None):
        if session_id and not attempt.session_id:
            attempt.session_id = str(session_id)
            attempt.save(update_fields=["session_id", "updated_at"])
        return cls.transition(attempt, "running", actor=actor, detail=detail)

    @classmethod
    def mark_output_published(cls, attempt, *, output=None, detail=None):
        return cls.transition(attempt, "output_published", output=output, detail=detail)

    @classmethod
    def complete(cls, attempt, *, output=None, detail=None):
        return cls.transition(attempt, "completed", output=output, detail=detail)

    @classmethod
    def fail(cls, attempt, *, error_code="", error_summary="", detail=None):
        return cls.transition(
            attempt, "failed", error_code=error_code,
            error_summary=error_summary, detail=detail,
        )

    @classmethod
    def cancel(cls, attempt, *, error_summary="", detail=None):
        return cls.transition(attempt, "cancelled", error_summary=error_summary, detail=detail)

    @classmethod
    def timeout(cls, attempt, *, error_summary="", detail=None):
        return cls.transition(attempt, "timed_out", error_summary=error_summary, detail=detail)

    # ------------------------------------------------------------------ 重试

    @classmethod
    def retry(cls, attempt, *, actor=None, idempotency_key="", detail=None):
        """重试：**新建**一条 attempt 并指回原记录，原记录保持终态不动。

        为什么不直接复用原记录：把 ``failed`` 改回 ``running`` 会让这次失败从
        记录里消失，"重试过几次、每次卡在哪"就再也查不出来——而重试链正是
        归因"是环境问题还是 Skill 问题"的主要证据。

        默认幂等键取 ``retry:<原 attempt id>``：对同一条失败记录连点重试仍然只有
        一条新 attempt。
        """
        if not attempt.is_terminal:
            raise ValidationError(
                f"执行尝试仍处于 {attempt.status}，请先结束或取消后再重试"
            )
        return cls.dispatch(
            project=attempt.project,
            workflow_id=attempt.workflow_id,
            stage=attempt.stage,
            skill_version=attempt.skill_version,
            parent_output_ids=attempt.parent_output_ids,
            entry_type=attempt.entry_type,
            session_id=attempt.session_id,
            idempotency_key=str(idempotency_key or f"retry:{attempt.pk}"),
            actor=actor,
            detail={**(attempt.detail or {}), **(detail or {}), "retry_of": str(attempt.pk)},
            flywheel_run=attempt.flywheel_run,
            retry_of=attempt,
        )


class StageExecutionContextService:
    """阶段执行上下文的签发与解析（T02 / R3）。

    上下文是"飞轮控制面"与"业务页面执行面"之间的**唯一接口**：飞轮把这一轮
    全部可信参数写在这里，业务页面只拿一个不透明 id。这样做的直接后果是——
    前端再也拿不到、也改不了 `skill_version_id`，篡改 URL 只能篡改那个 id，
    而 id 解析时要过项目、成员、过期、版本锁四道校验。

    为什么解析必须**幂等**：业务页面会在挂载时解析一次、刷新再解析一次、
    上下文失效重试时还会解析。把"解析"设计成一次性消费，会让第二次刷新
    直接失败——而用户根本没做错什么。所以这里只累加 `resolve_count` 做审计，
    不影响有效性；真正决定"还能不能用"的是 `expires_at` 与版本锁是否仍一致。
    """

    #: 允许写进上下文的**参数白名单**。
    #:
    #: 业务页面读回这些参数用于回显，因此它们会被页面渲染出来。白名单之外
    #: 一律丢弃：Agent 的原始请求体里有凭据、绝对路径、完整知识正文，
    #: 整包落库等于把它们复制到一张"任何项目成员都能解析"的表里。
    PAYLOAD_KEYS = frozenset({
        "module_key", "workflow_stage_order", "input_summary",
        "use_knowledge_base", "knowledge_base_ids", "knowledge_document_ids",
        "prompt_id", "requirement_document_ids",
    })

    @classmethod
    def _sanitize_payload(cls, payload) -> dict:
        return {
            name: value for name, value in (payload or {}).items()
            if name in cls.PAYLOAD_KEYS
        }

    @classmethod
    def issue(
        cls, *, attempt, project, actor=None, channel="", module_key="",
        skill_version=None, parent_output_ids=None, payload=None,
        ttl_minutes: int = EXECUTION_CONTEXT_TTL_MINUTES,
    ) -> StageExecutionContext:
        """签发一份上下文。

        ``skill_version`` 缺省时回落到 attempt 上的版本：attempt 已经解析过一次锁，
        再让调用方传一遍只会多一个"传漏了就变成无版本上下文"的机会。
        """
        version = skill_version if skill_version is not None else attempt.skill_version
        return StageExecutionContext.objects.create(
            project=project,
            flywheel_run=attempt.flywheel_run,
            attempt=attempt,
            workflow_id=attempt.workflow_id,
            stage=attempt.stage,
            entry_type=attempt.entry_type,
            channel=str(channel or ""),
            module_key=str(module_key or ""),
            skill_version=version,
            skill_package_sha256=(
                str(getattr(version, "package_sha256", "") or "")
                or str(attempt.skill_package_sha256 or "")
            ),
            parent_output_ids=[
                str(item) for item in (parent_output_ids or attempt.parent_output_ids or []) if item
            ],
            payload=cls._sanitize_payload(payload),
            issued_to=actor if getattr(actor, "pk", None) else None,
            expires_at=timezone.now() + timedelta(minutes=max(1, int(ttl_minutes or 1))),
        )

    @classmethod
    def _assert_member(cls, user, project_id) -> None:
        from rest_framework.exceptions import PermissionDenied

        if getattr(user, "is_superuser", False):
            return
        if not ProjectMember.objects.filter(user=user, project_id=project_id).exists():
            raise PermissionDenied("无权访问该项目飞轮数据")

    @classmethod
    def resolve(cls, *, context_id, project_id, user) -> StageExecutionContext:
        """解析上下文，返回**可信**的流程参数。四道校验全过才放行。

        1. **存在性**：「不存在」与「存在但属于别的项目」返回同一个结论。
           分开报错会让接口变成一个探测别项目是否存在的工具——只要 id 猜对就能
           从 404/403 的差别里读出信息。
        2. **过期**：过期不是错误，是"该重新派发了"，所以单独给一条可恢复的提示。
        3. **成员权限**：拿上下文的人必须是该项目成员。
        4. **版本锁一致性**：回查 ``WorkflowSkillLock``，若上下文记录的版本
           与流程当前锁定的版本不一致，拒绝——这正是"篡改 URL 不能替换锁定版本"
           这条要求的落点：即使有人拿到旧上下文的 id，旧版本也换不回流程。
        """
        from rest_framework.exceptions import ValidationError as DrfValidationError

        try:
            context_id = uuid.UUID(str(context_id))
        except (ValueError, TypeError, AttributeError):
            raise DrfValidationError("执行上下文不存在或已失效")

        context = StageExecutionContext.objects.select_related(
            "project", "attempt", "skill_version__skill", "flywheel_run",
        ).filter(pk=context_id).first()
        # 「不存在」与「跨项目」合并：不泄漏"这个 id 在别的项目里存在"。
        if context is None or context.project_id != int(project_id):
            raise DrfValidationError("执行上下文不存在或已失效")

        cls._assert_member(user, context.project_id)

        if context.is_expired():
            raise DrfValidationError(
                "执行上下文已过期，请回到质量飞轮重新派发本阶段"
            )

        lock = WorkflowSkillLock.objects.filter(
            project_id=context.project_id, workflow_id=context.workflow_id,
            lock_key=f"stage:{context.stage}",
        ).first()
        if lock is not None and str(lock.skill_version_id or "") != str(context.skill_version_id or ""):
            raise DrfValidationError(
                "本阶段锁定的 Skill 版本已变更，该上下文不再可信，请回到质量飞轮重新派发"
            )

        # 解析是审计行为，不是消费行为：只累加计数，不改变有效性。
        StageExecutionContext.objects.filter(pk=context.pk).update(
            last_resolved_at=timezone.now(),
            resolve_count=F("resolve_count") + 1,
        )
        context.refresh_from_db(fields=["last_resolved_at", "resolve_count"])
        return context

    @classmethod
    def view(cls, context: StageExecutionContext) -> dict:
        """把上下文摊平成业务页面直接可用的结构（T02 / §4.3）。

        刻意**不含** attempt 的失败摘要与 output 正文：页面这一步只需要"我要跑什么"，
        执行结果走 attempt / output 的查询接口。混在一起会让"解析上下文"变成一个
        权限口径更大的读接口。
        """
        version = context.skill_version
        return {
            "execution_context_id": str(context.pk),
            "project": context.project_id,
            "workflow_id": context.workflow_id,
            "stage": context.stage,
            "stage_label": WorkflowGateService.STAGE_LABELS.get(context.stage, context.stage),
            "entry_type": context.entry_type,
            "channel": context.channel,
            "module_key": context.module_key,
            "attempt_id": str(context.attempt_id),
            "attempt_status": (
                context.attempt.status if context.attempt_id else ""
            ),
            "parent_output_ids": list(context.parent_output_ids or []),
            "managed": bool(context.skill_version_id),
            "skill": {
                "skill_id": str(getattr(version, "skill_id", "") or ""),
                "skill_name": (
                    version.skill.name if version is not None and version.skill_id else ""
                ),
                "skill_version_id": str(context.skill_version_id or ""),
                "version": str(getattr(version, "version", "") or ""),
                "package_sha256": str(context.skill_package_sha256 or ""),
            },
            "payload": dict(context.payload or {}),
            "expires_at": context.expires_at.isoformat(),
            "expired": context.is_expired(),
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
                    # 与 ``workflow-status`` 同源：「下载报告」按钮的显示条件与悬浮提示
                    # 都读它。控制台的卡片才是用户真正点下载的地方，缺了这块，
                    # 卡片上就只能给一个不说清"下到的是登记产物还是回落文本"的按钮。
                    "artifact": (
                        WorkflowGateService.stage_artifact_payload(output, stage=stage)
                        if output is not None else None
                    ),
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
