"""旁路产出的纳管与替换（T12 / §9）。

背景：阶段产出有两条来路。

- **受控执行**：从飞轮发起流程 → 受控执行阶段 → 产出发布时自动挂门禁、锁版本。
- **旁路生成**：人直接在业务页面（如测试方案生成页）点一次生成，产出同样入库、
  同样有 ``stage_result``，但它不属于任何流程 —— 于是在质量飞轮里"看不见"，
  既不能评审、也不能归因，更不能进化。

纳管就是把第二类接进第一类。三件事必须成立，否则这个动作比不做更糟：

1. **不改产出本身**。``GenerationOutput.metadata`` 是产出当时的协议快照，
   事后补写 ``workflow_id`` 会让一份旁路产出看起来从来就在受控流程里，
   历史从此不可信。纳管在 ``WorkflowStageSubmission`` 上新增一条绑定。
2. **版本必须与流程锁一致**。流程锁是"这条链路的产出对应哪个包"的唯一答案；
   纳进来的产出若来自另一个版本，之后所有归因与候选都会指向一个错的对象。
3. **替换必须显式确认**。同阶段已经有一份产出时，静默顶掉它等于悄悄作废
   一次已完成的评审与门禁结论。所以要求显式 ``confirm_replace`` + 指明替换对象，
   并把旧门禁的结论快照留档。首期**不做同阶段并行分支**：两条并存的分支会让
   "这个阶段的产出是哪一份"没有唯一答案。
"""
from __future__ import annotations

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from .capability_registry import ALL_WORKFLOW_STAGES, STAGE_LABELS
from .workflow_models import (
    SUBMISSION_STATE_ADMITTED,
    SUBMISSION_STATE_SUPERSEDED,
    SUBMISSION_TARGET_EXISTING,
    SUBMISSION_TARGET_NEW,
    SUBMISSION_TARGETS,
    SUBMISSION_TARGET_LABELS,
    WorkflowSkillLock,
    WorkflowStageGate,
    WorkflowStageSubmission,
)

#: 阶段归属真值 = 新主链路 ∪ 历史主链路（与门禁 choices 同一来源）。
SUBMISSION_STAGES: tuple[str, ...] = tuple(ALL_WORKFLOW_STAGES)

logger = logging.getLogger(__name__)

#: 拒绝原因码。给码而不是只给一句话：前端要据此分流 ——
#: "需要确认替换"是可继续的动作（弹确认框），其余是真拒绝（直接报错）。
REFUSE_UNKNOWN_STAGE = "unknown_stage"
REFUSE_CROSS_PROJECT = "cross_project"
REFUSE_UNKNOWN_TARGET = "unknown_target"
REFUSE_WORKFLOW_NOT_FOUND = "workflow_not_found"
REFUSE_WORKFLOW_ALREADY_EXISTS = "workflow_already_exists"
REFUSE_VERSION_MISMATCH = "version_mismatch"
REFUSE_PARENT_MISSING = "parent_missing"
REFUSE_STAGE_CONFLICT = "stage_conflict"
REFUSE_REPLACE_NOT_CONFIRMED = "replace_not_confirmed"
REFUSE_REPLACE_TARGET_MISMATCH = "replace_target_mismatch"

#: 需要显式确认才继续的码（前端据此弹确认，而不是当硬错误）。
CONFIRMABLE_CODES: frozenset[str] = frozenset({
    REFUSE_STAGE_CONFLICT, REFUSE_REPLACE_NOT_CONFIRMED,
})


class SubmissionRefused(Exception):
    """纳管被拒。带 ``code`` 与完整 ``analysis``，让调用方能解释**为什么**。

    只带一句话的异常会让前端只能弹"纳管失败"，用户既不知道是不是要确认替换，
    也不知道该改哪个参数 —— 而这正是本层最容易让人放弃的地方。
    """

    def __init__(self, message: str, *, code: str = "", analysis: dict | None = None) -> None:
        self.code = code
        self.analysis = analysis or {}
        self.message = message
        super().__init__(message)

    @property
    def confirmable(self) -> bool:
        return self.code in CONFIRMABLE_CODES

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "code": self.code,
            "confirmable": self.confirmable,
            "analysis": self.analysis,
        }


class WorkflowSubmissionService:
    """旁路产出的纳管、预检与替换。只读写飞轮自己的表，**不改产出协议**。"""

    # ------------------------------------------------------------------ 权限

    @staticmethod
    def _assert_member(user, project_id) -> None:
        from .operations import StageExecutionContextService

        StageExecutionContextService._assert_member(user, project_id)

    # ------------------------------------------------------------------ 预检

    @classmethod
    def analyze(
        cls, *, project, output, stage: str = "", target: str = SUBMISSION_TARGET_EXISTING,
        workflow_id: str = "", replace_output_id: str = "",
    ) -> dict:
        """纳管前的完整判定（不写库）。

        ``admit`` 复用同一份判定，所以"预检说能纳管、提交却被拒"这种口径分叉
        在结构上不可能发生 —— 前端可以放心把预检结果当成按钮的可用性依据。
        """
        stage = str(stage or cls._stage_of(output) or "").strip()
        target = str(target or SUBMISSION_TARGET_EXISTING)
        workflow_id = str(workflow_id or "").strip()
        replace_output_id = str(replace_output_id or "")

        codes: list[str] = []
        messages: list[str] = []

        def issue(code: str, message: str) -> None:
            # 码与话**一一对齐**：前端按码分流、按话展示。错位会让"需要确认替换"
            # 显示成"版本不一致"这种完全无关的提示，用户照着改还是过不去。
            codes.append(code)
            messages.append(message)

        if stage not in SUBMISSION_STAGES:
            issue(REFUSE_UNKNOWN_STAGE, f"未知的阶段：{stage or '（空）'}")
        if target not in SUBMISSION_TARGETS:
            issue(REFUSE_UNKNOWN_TARGET, f"未知的纳管去处：{target}")
        if str(getattr(output, "project_id", "")) != str(getattr(project, "pk", "")):
            issue(REFUSE_CROSS_PROJECT, "该产出不属于当前项目，不能纳管")

        workflow_exists = False
        if target == SUBMISSION_TARGET_NEW:
            if not workflow_id:
                workflow_id = cls._generate_workflow_id()
            elif cls._workflow_exists(project_id=project.pk, workflow_id=workflow_id):
                issue(
                    REFUSE_WORKFLOW_ALREADY_EXISTS,
                    f"流程 {workflow_id} 已存在；纳入新流程请不要复用已有流程编号",
                )
        elif target == SUBMISSION_TARGET_EXISTING:
            if not workflow_id:
                issue(REFUSE_WORKFLOW_NOT_FOUND, "纳入已有流程必须提供 workflow_id")
            else:
                workflow_exists = cls._workflow_exists(
                    project_id=project.pk, workflow_id=workflow_id,
                )
                if not workflow_exists:
                    issue(REFUSE_WORKFLOW_NOT_FOUND, f"流程 {workflow_id} 在本项目不存在，无法纳入")

        version_lock = cls._version_lock(
            project_id=project.pk, workflow_id=workflow_id, stage=stage, output=output,
        )
        if version_lock["locked"] and not version_lock["matches"]:
            issue(
                REFUSE_VERSION_MISMATCH,
                f"产出绑定的 Skill 版本（{version_lock['output_version'] or '未记录'}）"
                f"与流程锁定的版本（{version_lock['version'] or '未记录'}）不一致，不能纳管",
            )

        parents = cls._parents(output)
        if parents["missing"]:
            issue(
                REFUSE_PARENT_MISSING,
                f"上游产出不存在或不属于本项目：{'、'.join(parents['missing'])}",
            )

        conflict = cls._stage_conflict(
            project_id=project.pk, workflow_id=workflow_id, stage=stage, output=output,
        )
        requires_replace_confirmation = conflict is not None
        if conflict is not None:
            # 首期不做同阶段并行分支：已有一份产出就是冲突，必须显式替换。
            issue(
                REFUSE_STAGE_CONFLICT,
                "该阶段已有产出；纳管将替换它，需显式指定被替换的产出并确认",
            )
            if replace_output_id and replace_output_id != str(conflict["output_id"]):
                issue(
                    REFUSE_REPLACE_TARGET_MISMATCH,
                    "指定的被替换产出与当前占用该阶段的产出不一致",
                )

        return {
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "target": target,
            "target_label": SUBMISSION_TARGET_LABELS.get(target, target),
            "workflow_id": workflow_id,
            "workflow_exists": workflow_exists,
            "output_id": str(getattr(output, "pk", "")),
            "output_skill_version_id": str(getattr(output, "skill_version_id", "") or ""),
            "admissible": not codes,
            "codes": codes,
            "messages": messages,
            "stage_conflict": conflict,
            "requires_replace_confirmation": requires_replace_confirmation,
            "version_lock": version_lock,
            "parents": parents,
        }

    # ------------------------------------------------------------------ 纳管

    @classmethod
    @transaction.atomic
    def admit(
        cls, *, project, output, actor, stage: str = "", target: str = SUBMISSION_TARGET_EXISTING,
        workflow_id: str = "", replace_output_id: str = "", confirm_replace: bool = False,
        note: str = "",
    ) -> dict:
        """把一份旁路产出纳管进流程（幂等）。

        Args:
            replace_output_id: 明确要替换掉的旧产出 id。**必须显式给**，
                不允许"默认替换当前那一份" —— 那等于把"作废一次评审"变成默认行为。
            confirm_replace: 替换确认。与 ``replace_output_id`` 两个条件都要满足。
        """
        cls._assert_member(actor, project.pk)
        stage = str(stage or cls._stage_of(output) or "").strip()
        analysis = cls.analyze(
            project=project, output=output, stage=stage, target=target,
            workflow_id=workflow_id, replace_output_id=replace_output_id,
        )
        # 硬错优先上报：同时命中"要确认替换"与"替换对象不对"时，先告诉用户参数错了
        # —— 让他去确认一次注定失败的替换是纯浪费。
        # 可确认的码（``stage_conflict``）**不在这里抛**：它是"再确认一次就能继续"
        # 的动作，抛在这里会让"确认替换"这个功能永远走不到。
        hard = [code for code in analysis["codes"] if code not in CONFIRMABLE_CODES]
        if hard:
            code = hard[0]
            raise SubmissionRefused(
                analysis["messages"][analysis["codes"].index(code)],
                code=code, analysis=analysis,
            )

        conflict = analysis["stage_conflict"]
        supersedes = None
        if conflict is not None:
            # 检测到冲突（``stage_conflict``）与"用户还没确认"是两件事：
            # 前者是状态，后者是下一步动作，抛给前端的码用后者更可执行。
            if not replace_output_id or replace_output_id != str(conflict["output_id"]):
                raise SubmissionRefused(
                    "替换已有产出需要显式指定被替换的对象",
                    code=REFUSE_REPLACE_NOT_CONFIRMED, analysis=analysis,
                )
            if not confirm_replace:
                raise SubmissionRefused(
                    "替换已有产出需要显式确认",
                    code=REFUSE_REPLACE_NOT_CONFIRMED, analysis=analysis,
                )
            supersedes = cls._output(project_id=project.pk, output_id=conflict["output_id"])

        resolved_workflow_id = analysis["workflow_id"]
        # 旧门禁的结论在挂新产出之前先快照下来。不先存这一步，
        # "保留旧门禁"就只剩一句注释：那份 gate 记录会被改指向新产出，
        # 它当时判过什么、谁判的，从此查不到。
        superseded_gate = cls._snapshot_gate(
            project_id=project.pk, workflow_id=resolved_workflow_id, stage=stage,
        )

        gate = cls._attach_gate(
            project=project, output=output, workflow_id=resolved_workflow_id, stage=stage,
        )

        now = timezone.now()
        submission, created = WorkflowStageSubmission.objects.get_or_create(
            project=project, output=output, stage=stage,
            defaults={
                "workflow_id": resolved_workflow_id,
                "target": target,
                "skill_version_id": getattr(output, "skill_version_id", None),
                "package_sha256": getattr(output, "skill_package_sha256", "") or "",
                "supersedes_output": supersedes,
                "state": SUBMISSION_STATE_ADMITTED,
                "note": str(note or "")[:500],
                "detail": {
                    "superseded_gate": superseded_gate,
                    "history": [{
                        "action": "admitted",
                        "target": target,
                        "workflow_id": resolved_workflow_id,
                        "supersedes_output_id": str(getattr(supersedes, "pk", "") or ""),
                        "actor_id": getattr(actor, "pk", None),
                        "at": now.isoformat(),
                    }],
                },
                "created_by": actor if getattr(actor, "pk", None) else None,
            },
        )
        if not created:
            # 幂等重纳管：只把绑定更新到最新状态，不追加重复的历史条目。
            history = list((submission.detail or {}).get("history") or [])
            history.append({
                "action": "readmitted",
                "target": target,
                "actor_id": getattr(actor, "pk", None),
                "at": now.isoformat(),
            })
            submission.workflow_id = resolved_workflow_id
            submission.target = target
            submission.skill_version_id = getattr(output, "skill_version_id", None)
            submission.package_sha256 = getattr(output, "skill_package_sha256", "") or ""
            submission.supersedes_output = supersedes
            submission.state = SUBMISSION_STATE_ADMITTED
            submission.note = str(note or "")[:500] or submission.note
            submission.detail = {**(submission.detail or {}), "history": history}
            submission.save(update_fields=[
                "workflow_id", "target", "skill_version", "package_sha256",
                "supersedes_output", "state", "note", "detail", "updated_at",
            ])

        if supersedes is not None:
            # 被替换产出的绑定降级为"已被替换"：它的产出、反馈与归因都还在，
            # 只是不再代表这一阶段的当前版本。
            WorkflowStageSubmission.objects.filter(
                project=project, output=supersedes, stage=stage,
            ).exclude(pk=submission.pk).update(
                state=SUBMISSION_STATE_SUPERSEDED, updated_at=now,
            )

        payload = cls.view(submission)
        payload["created"] = created
        payload["gate_status"] = gate.status if gate is not None else ""
        payload["superseded_gate"] = superseded_gate
        return payload

    # ------------------------------------------------------------------ 查询

    @classmethod
    def list_for(
        cls, *, project_id, workflow_id: str = "", stage: str = "", state: str = "",
        limit: int = 100,
    ):
        queryset = WorkflowStageSubmission.objects.filter(project_id=project_id)
        if workflow_id:
            queryset = queryset.filter(workflow_id=str(workflow_id))
        if stage:
            queryset = queryset.filter(stage=str(stage))
        if state:
            queryset = queryset.filter(state=str(state))
        return queryset.select_related(
            "output", "skill_version", "supersedes_output", "created_by",
        )[: max(1, int(limit))]

    @classmethod
    def submission_for(cls, output, *, stage: str = ""):
        """该产出的纳管绑定（有则返回，无则 None）。供 lineage 显示"怎么进来的"。"""
        queryset = WorkflowStageSubmission.objects.filter(output=output)
        if stage:
            queryset = queryset.filter(stage=str(stage))
        return queryset.select_related("supersedes_output", "created_by").first()

    @classmethod
    def view(cls, submission: WorkflowStageSubmission) -> dict:
        """对外结构。只做形状转换，**不做权限判定**（调用方先确认成员身份）。"""
        return {
            "submission_id": str(submission.pk),
            "output_id": str(submission.output_id),
            "project": submission.project_id,
            "workflow_id": submission.workflow_id,
            "stage": submission.stage,
            "stage_label": STAGE_LABELS.get(submission.stage, submission.stage),
            "target": submission.target,
            "target_label": SUBMISSION_TARGET_LABELS.get(submission.target, submission.target),
            "state": submission.state,
            "state_label": submission.get_state_display(),
            "skill_version_id": str(submission.skill_version_id or ""),
            "version": (
                submission.skill_version.version if submission.skill_version_id else ""
            ),
            "package_sha256": submission.package_sha256,
            "supersedes_output_id": str(submission.supersedes_output_id or ""),
            "note": submission.note,
            # 协议历史**从未被改写**：这一点由产出自身的 metadata 复核，
            # 页面据此显示"产出元数据未被纳管修改"。
            "output_protocol_untouched": True,
            "superseded_gate": submission.gate_snapshot(),
            "history": list(reversed((submission.detail or {}).get("history") or [])),
            "created_by": (
                submission.created_by.username if submission.created_by_id else ""
            ),
            "created_at": submission.created_at.isoformat(),
        }

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _stage_of(output) -> str:
        protocol = (getattr(output, "metadata", None) or {}).get("protocol") or {}
        return str(protocol.get("stage") or getattr(output, "task_type", "") or "")

    @staticmethod
    def _generate_workflow_id() -> str:
        return f"wf-{uuid.uuid4().hex[:12]}"

    @classmethod
    def _attach_gate(cls, *, project, output, workflow_id: str, stage: str):
        """把产出挂到"该流程该阶段"的门禁上。

        不能借 ``WorkflowGateService.register_output``：它按**产出 protocol 里的**
        ``workflow_id`` 判阶段归属，而旁路产出的协议里没有 workflow_id（也不该有
        —— 协议是产出当时的快照），于是它会直接 no-op。后果不是报错，而是
        "纳管显示成功、门禁却没建"，下一步点"评测"时才发现找不到对象。
        这里按**纳管目标流程**显式挂载。
        """
        from .operations import WorkflowGateService

        # 补锁：产出已存在、协议里没记流程，只能按纳管目标锁版本。
        WorkflowGateService.ensure_stage_lock(output, workflow_id=workflow_id, stage=stage)

        gate = WorkflowStageGate.objects.filter(
            project=project, workflow_id=workflow_id, stage=stage,
        ).first()
        if gate is None:
            gate = WorkflowStageGate.objects.create(
                project=project, workflow_id=workflow_id, stage=stage,
                output=output, status="pending",
            )
        elif gate.output_id != output.pk:
            # 换了产出 = 这一阶段重跑过：新内容必须重新过门禁，旧结论不能顺延。
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
        WorkflowGateService._sync_report_contract(gate)

        # 候选沉淀（T04 / P5）：入队幂等，只入队不建候选。best-effort ——
        # 入队失败不该让"纳管"这个动作失败，它是旁路产出进流程的唯一机会。
        try:
            from .gold import AssetCandidateService

            AssetCandidateService.enqueue_from_output(output)
        except Exception:  # noqa: BLE001
            logger.exception("纳管产出入候选队列失败，不影响纳管结果")
        return gate

    @staticmethod
    def _output(*, project_id, output_id):
        from .models import GenerationOutput

        return GenerationOutput.objects.filter(
            pk=output_id, project_id=project_id,
        ).first()

    @classmethod
    def _workflow_exists(cls, *, project_id, workflow_id: str) -> bool:
        from .models import FlywheelRun
        from .workflow_models import StageExecutionAttempt

        return any([
            FlywheelRun.objects.filter(project_id=project_id, workflow_id=workflow_id).exists(),
            WorkflowStageGate.objects.filter(
                project_id=project_id, workflow_id=workflow_id,
            ).exists(),
            WorkflowSkillLock.objects.filter(
                project_id=project_id, workflow_id=workflow_id,
            ).exists(),
            StageExecutionAttempt.objects.filter(
                project_id=project_id, workflow_id=workflow_id,
            ).exists(),
            WorkflowStageSubmission.objects.filter(
                project_id=project_id, workflow_id=workflow_id,
            ).exists(),
        ])

    @staticmethod
    def _version_lock(*, project_id, workflow_id: str, stage: str, output) -> dict:
        lock = WorkflowSkillLock.objects.filter(
            project_id=project_id, workflow_id=workflow_id, lock_key=f"stage:{stage}",
        ).select_related("skill_version").first() if workflow_id else None
        output_version_id = str(getattr(output, "skill_version_id", "") or "")
        if lock is None:
            # 没有锁不是拒绝理由：``register_output`` 会在纳管时补锁。
            return {
                "locked": False, "matches": True,
                "skill_version_id": "", "version": "",
                "output_version_id": output_version_id, "output_version": "",
            }
        from skills.models import SkillVersion

        output_version = (
            SkillVersion.objects.filter(pk=output.skill_version_id).first()
            if output.skill_version_id else None
        )
        locked_version_id = str(lock.skill_version_id or "")
        return {
            "locked": True,
            "matches": bool(output_version_id) and output_version_id == locked_version_id,
            "skill_version_id": locked_version_id,
            "version": lock.skill_version.version if lock.skill_version_id else "",
            "output_version_id": output_version_id,
            "output_version": output_version.version if output_version is not None else "",
        }

    @staticmethod
    def _parents(output) -> dict:
        from .models import GenerationOutput

        protocol = (getattr(output, "metadata", None) or {}).get("protocol") or {}
        parent_ids = [str(item) for item in (protocol.get("parent_output_ids") or []) if str(item or "")]
        existing = set(
            str(pk) for pk in GenerationOutput.objects.filter(
                pk__in=parent_ids, project_id=output.project_id,
            ).values_list("pk", flat=True)
        )
        return {
            "total": len(parent_ids),
            "missing": [item for item in parent_ids if item not in existing],
        }

    @staticmethod
    def _stage_conflict(*, project_id, workflow_id: str, stage: str, output) -> dict | None:
        """该阶段是否已被**另一份**产出占用。占用即冲突（首期不做并行分支）。"""
        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        ).select_related("output").first() if workflow_id else None
        occupying = gate.output if gate is not None and gate.output_id else None
        if occupying is None:
            # 门禁还没建但已有绑定（理论上不该发生）也要算冲突，
            # 否则会绕过"不并行"的约束再建一条分支。
            other = WorkflowStageSubmission.objects.filter(
                project_id=project_id, workflow_id=workflow_id, stage=stage,
                state=SUBMISSION_STATE_ADMITTED,
            ).exclude(output=output).select_related("output").first()
            occupying = other.output if other is not None else None
        if occupying is None or str(occupying.pk) == str(getattr(output, "pk", "")):
            return None
        return {
            "output_id": str(occupying.pk),
            "task_type": occupying.task_type,
            "created_at": occupying.created_at.isoformat() if occupying.created_at else "",
            "gate_status": gate.status if gate is not None else "",
        }

    @staticmethod
    def _snapshot_gate(*, project_id, workflow_id: str, stage: str) -> dict:
        """把当前门禁结论留档（供"旧门禁保留"这一条可被追查）。"""
        gate = WorkflowStageGate.objects.filter(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        ).select_related("output").first() if workflow_id else None
        if gate is None:
            return {}
        return {
            "output_id": str(gate.output_id or ""),
            "status": gate.status,
            "scores": gate.scores or {},
            "reason": gate.reason,
            "decided_by": (
                gate.decided_by.username if getattr(gate, "decided_by_id", None) else ""
            ),
            "decided_at": gate.decided_at.isoformat() if gate.decided_at else "",
            "snapshot_at": timezone.now().isoformat(),
        }


__all__ = [
    "SUBMISSION_STAGES",
    "REFUSE_UNKNOWN_STAGE", "REFUSE_CROSS_PROJECT", "REFUSE_UNKNOWN_TARGET",
    "REFUSE_WORKFLOW_NOT_FOUND", "REFUSE_WORKFLOW_ALREADY_EXISTS",
    "REFUSE_VERSION_MISMATCH", "REFUSE_PARENT_MISSING", "REFUSE_STAGE_CONFLICT",
    "REFUSE_REPLACE_NOT_CONFIRMED", "REFUSE_REPLACE_TARGET_MISMATCH",
    "CONFIRMABLE_CODES",
    "SubmissionRefused", "WorkflowSubmissionService",
]
