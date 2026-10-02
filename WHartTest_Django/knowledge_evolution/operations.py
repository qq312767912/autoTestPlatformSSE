"""任务 17–19：防腐检查、联合链路图和运营指标。"""
import logging
from collections import Counter

from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from projects.models import ProjectMember

from .capability_models import CapabilityRelease
from .evaluation_models import EvaluationResult, EvaluationRun
from .graph import GraphEdgeSpec, GraphNodeSpec, PostgreSQLGraphSource
from .graph_models import GraphEdge, GraphNode
from .knowledge_models import IndexProjection, KnowledgeConflict, KnowledgeVersion
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .gold_models import GoldDataset
from .workflow_models import WorkflowSkillLock, WorkflowStageGate

logger = logging.getLogger(__name__)

WORKFLOW_STAGE_ORDER = [
    "test_plan_generation", "testcase_generation", "test_execution", "report_generation",
]


def _without_timestamp(payload: dict) -> dict:
    """去掉 ``checked_at`` 后比对，用来判断结论本身有没有变。"""
    return {key: value for key, value in (payload or {}).items() if key != "checked_at"}


class WorkflowGateService:
    """统一维护四阶段 Skill 门禁；前端状态和协议校验都读取同一真值。"""

    @classmethod
    def register_output(cls, output):
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = protocol.get("workflow_id")
        stage = protocol.get("stage") or output.task_type
        if not workflow_id or stage not in WORKFLOW_STAGE_ORDER:
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
        if gate.stage != "report_generation":
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
        if not workflow_id or stage not in WORKFLOW_STAGE_ORDER:
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
        if stage not in WORKFLOW_STAGE_ORDER:
            return
        position = WORKFLOW_STAGE_ORDER.index(stage)
        if position == 0:
            return
        previous = WORKFLOW_STAGE_ORDER[position - 1]
        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=previous
        ).first()
        if gate is not None and gate.status in {"passed", "overridden"}:
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
        raise ValidationError(
            f"上一阶段 {previous} 门禁尚未测评（当前状态：{gate.get_status_display()}），"
            f"无法进入 {stage}"
        )

    @staticmethod
    def evaluate(gate, actor=None):
        # T16：报告阶段先过**确定性契约**，再谈分层评分。
        # 顺序不能反：一份引用了不存在产出、或压根没写"未闭环问题"的报告，
        # 即便文案评分满分也不该放行——它的分数建立在无法核验的依据上。
        # 契约不过就直接判失败并说明缺什么，避免"分数很高但报告是空的"这种放行。
        if gate.stage == "report_generation" and gate.output_id:
            from .report_gates import ReportGateService

            contract = ReportGateService.validate(gate.output)
            gate.detail = {**(gate.detail or {}), "report_contract": contract}
            if not contract["ok"]:
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
        gate.scores = scores
        gate.status = "passed" if scores and all(score >= gate.threshold for score in scores.values()) else "failed"
        gate.reason = "分层评测自动计算" if scores else "没有可用于门禁判断的已完成评测结果"
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

    # ------------------------------------------------- T15：链路启动与状态真值

    #: 阶段中文名，供前端与错误提示共用，避免各处自己写一份。
    STAGE_LABELS = {
        "test_plan_generation": "测试方案",
        "testcase_generation": "测试用例",
        "test_execution": "测试执行",
        "report_generation": "报告生成",
    }

    @classmethod
    def start_workflow(cls, *, project, workflow_id, actor=None, stages=None,
                       allow_unmanaged: bool = True) -> dict:
        """启动四阶段流水线：**一次性锁定四个阶段的 Skill 版本**。

        为什么在启动时就把四个阶段全锁掉，而不是走到哪锁到哪：

        1. 链路跑起来之后随时可能有人激活新版本。如果每阶段等它自己开始时才解析，
           同一条流水线的四个阶段可能分别用到四份不同的包，"这条链路的产出对应哪个版本"
           就无法回答，回滚也界定不了影响范围。
        2. 启动时一次性解析，能把"某个阶段的能力包没准备好"在**入口处**暴露出来。
           否则问题要到跑了两小时之后的报告阶段才炸，前面阶段的算力与人工全部白费。

        Returns:
            锁定结果；``unmanaged_stages`` 列出项目尚未登记 Skill 的阶段——
            这些阶段按平台默认行为执行、没有版本溯源，页面要能看到这个事实。
        """
        from .task_binding import TaskSkillBindingService

        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise ValidationError("启动四阶段流水线必须提供 workflow_id")
        targets = list(stages or WORKFLOW_STAGE_ORDER)
        unknown = [item for item in targets if item not in WORKFLOW_STAGE_ORDER]
        if unknown:
            raise ValidationError(f"未知的阶段：{'、'.join(unknown)}")

        bindings: dict[str, dict] = {}
        for stage in WORKFLOW_STAGE_ORDER:
            if stage not in targets:
                continue
            binding = TaskSkillBindingService.bind_stage(
                project=project, workflow_id=workflow_id, stage=stage,
                actor=actor, allow_unmanaged=allow_unmanaged,
            )
            bindings[stage] = binding
        return {
            "workflow_id": workflow_id,
            "stage_order": list(WORKFLOW_STAGE_ORDER),
            "bindings": {
                stage: cls._binding_view(binding) for stage, binding in bindings.items()
            },
            "locked_stages": [s for s, b in bindings.items() if b["managed"]],
            "unmanaged_stages": [s for s, b in bindings.items() if not b["managed"]],
        }

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
        for index, stage in enumerate(WORKFLOW_STAGE_ORDER):
            gate = gates.get(stage)
            output = outputs.get(stage)
            lock = locks.get(stage)
            entered = output is not None or gate is not None
            if not entered:
                # 还没轮到的阶段：只看上一阶段是否已放行。
                previous = (
                    None if index == 0
                    else gates.get(WORKFLOW_STAGE_ORDER[index - 1])
                )
                can_enter = index == 0 or bool(
                    previous and previous.status in {"passed", "overridden"}
                )
                state = "ready" if can_enter else "blocked"
                if not can_enter and not blocked_at:
                    blocked_at = stage
            else:
                can_enter = True
                state = gate.status if gate is not None else "pending"
            stages.append({
                "stage": stage,
                "label": cls.STAGE_LABELS.get(stage, stage),
                "state": state,
                "entered": entered,
                "can_enter": can_enter,
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
                    # T16：报告契约与阶段/端到端评测结论随门禁一起回给页面，
                    # 状态和证据同源，避免页面自己再算一遍算出第二种结论。
                    "report_contract": (gate.detail or {}).get("report_contract"),
                    "evaluation": (gate.detail or {}).get("evaluation"),
                } if gate is not None else None),
                "version": (lock or None),
            })
        report = gates.get(WORKFLOW_STAGE_ORDER[-1])
        report_output = outputs.get(WORKFLOW_STAGE_ORDER[-1])
        report_contract = None
        if report is not None:
            report_contract = (report.detail or {}).get("report_contract")
        if report_contract is None and report_output is not None:
            # 有报告产出但门禁还没建（例如旁路产出）时现算一份，避免页面显示"无契约"。
            from .report_gates import ReportGateService

            report_contract = ReportGateService.validate(report_output)
        return {
            "workflow_id": workflow_id,
            "stage_order": list(WORKFLOW_STAGE_ORDER),
            "stages": stages,
            "locked_version_count": len(locks),
            "managed": bool(locks),
            # 契约结论随状态一起回给页面；页面据此解释"为什么报告阶段还没过"。
            # 但它**不参与** completed 判定：报告门禁只有走到 passed 才可能通过契约
            # （见 ``evaluate``），而负责人留痕放行（overridden）是人的决定，
            # 不能让契约反过来否决——这会把"留痕放行"变成一句空话。
            "report_contract": report_contract,
            "completed": bool(
                report is not None and report.status in {"passed", "overridden"}
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
            "detail": binding.get("detail", ""),
        }


class ProjectQualityCockpitService:
    SINGLE_STAGES = ["case_review", "code_review", "knowledge_query"]

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
            })

        workflow_outputs = outputs.filter(
            task_type__in=WORKFLOW_STAGE_ORDER
        ).exclude(metadata__protocol__workflow_id="").select_related(
            "capability__active_release"
        ).order_by("created_at")
        workflow_ids = []
        grouped = {}
        for output in workflow_outputs:
            protocol = (output.metadata or {}).get("protocol") or {}
            workflow_id = str(protocol.get("workflow_id") or "")
            if not workflow_id:
                continue
            if workflow_id not in grouped:
                workflow_ids.append(workflow_id)
                grouped[workflow_id] = {}
            grouped[workflow_id][protocol.get("stage") or output.task_type] = output
        gates = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id__in=workflow_ids
        ).select_related("decided_by")
        gate_map = {(gate.workflow_id, gate.stage): gate for gate in gates}
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
            stages = []
            for stage in WORKFLOW_STAGE_ORDER:
                output = grouped[workflow_id].get(stage)
                gate = gate_map.get((workflow_id, stage))
                if output and not gate:
                    gate = WorkflowGateService.register_output(output)
                if output:
                    status = gate.status if gate else "pending"
                else:
                    status = "ready" if previous_open else "blocked"
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
                    "reason": gate.reason if gate else "",
                    "decided_by": gate.decided_by.username if gate and gate.decided_by else "",
                    "skill_name": skill_name,
                    "skill_version": skill_version,
                    "version_locked": lock is not None,
                    "package_sha256": lock.package_sha256 if lock is not None else "",
                    "self_evolution": lock is not None or bool(output and output.capability_id),
                })
                previous_open = bool(output and gate and gate.status in {"passed", "overridden"})
            workflows.append({"workflow_id": workflow_id, "stages": stages})
        return {
            "people": {"leads": leads, "executors": executors},
            "gold_by_type": gold_by_type,
            "single_capabilities": singles,
            "workflows": workflows,
            "stage_order": WORKFLOW_STAGE_ORDER,
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
