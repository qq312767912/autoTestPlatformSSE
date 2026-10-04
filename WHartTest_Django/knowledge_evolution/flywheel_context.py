"""项目级飞轮运行上下文的创建与阶段解析。"""
from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .capability_registry import ALL_WORKFLOW_STAGES
from .models import GenerationOutput
from .workflow_models import FlywheelRun, WorkflowSkillLock


class FlywheelContextService:
    """让所有业务入口使用同一个可校验的项目/流程上下文。"""

    #: 各入口派生 workflow_id 时使用的前缀真值。放在服务层而不是让每个入口自己拼：
    #: 前缀一旦分叉，"需求文档 → 方案 → 用例"就会各自开出一条链，追溯就此断掉。
    ENTRY_WORKFLOW_PREFIX = {
        "requirement": "req",
        "chat": "chat",
        "test_management": "tm",
        "flywheel": "fly",
        "history_replay": "hist",
    }

    @staticmethod
    def _document_ids(project, document_ids) -> list[str]:
        values = list(dict.fromkeys(str(value) for value in (document_ids or [])))
        if not values:
            return []
        from requirements.models import RequirementDocument

        found = {
            str(value) for value in
            RequirementDocument.objects.filter(project=project, id__in=values)
            .values_list("id", flat=True)
        }
        missing = [value for value in values if value not in found]
        if missing:
            raise ValidationError(f"需求文档不属于当前项目或不存在：{missing}")
        return values

    @classmethod
    @transaction.atomic
    def create(cls, *, project, workflow_id: str, entry_type: str, actor=None,
               intent: str = "production", requirement_document_ids=None,
               metadata=None) -> FlywheelRun:
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            raise ValidationError("workflow_id 不能为空")
        documents = cls._document_ids(project, requirement_document_ids)
        run, created = FlywheelRun.objects.get_or_create(
            project=project,
            workflow_id=workflow_id,
            defaults={
                "entry_type": entry_type,
                "intent": intent,
                "requirement_document_ids": documents,
                "created_by": actor if getattr(actor, "pk", None) else None,
                "metadata": metadata or {},
            },
        )
        if not created:
            # 同一项目/流程只有一个权威上下文。重复入口只能补充需求文档，不能静默
            # 改写首次入口、意图或历史元数据。
            merged = list(dict.fromkeys([*run.requirement_document_ids, *documents]))
            if merged != run.requirement_document_ids:
                run.requirement_document_ids = merged
                run.save(update_fields=["requirement_document_ids", "updated_at"])
        return run

    @classmethod
    def derive_workflow_id(cls, entry_type: str, source_id) -> str:
        """按入口与业务对象标识派生一个**确定性**的 workflow_id。

        确定性是这里唯一重要的性质：同一个需求文档 / 同一个会话 / 同一次审查
        重复发起时必须落到同一条链上。若改用随机 ID，"重新进入"就会另开一条链，
        上一阶段的产出立刻变成孤儿——而这正是入口不统一时的典型故障。
        """
        entry = str(entry_type or "").strip()
        prefix = cls.ENTRY_WORKFLOW_PREFIX.get(entry, "wf")
        key = str(source_id or "").strip()
        if not key:
            if entry == "flywheel":
                # 人工入口没有业务对象可锚定，"确定性"无从谈起；给一个可读且唯一的
                # 标识（日期 + 短随机），页面就不必先逼用户编一个 workflow_id。
                key = f"{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
            else:
                raise ValidationError("缺少业务对象标识，无法派生 workflow_id")
        return f"{prefix}:{key}"

    @classmethod
    @transaction.atomic
    def open(cls, *, project, entry_type: str, source_id="", workflow_id: str = "",
             actor=None, intent: str = "production", requirement_document_ids=None,
             metadata=None) -> dict:
        """四类入口统一的「创建或选择流程上下文」（T06 / R1、R2）。

        入口只提供"我是谁（entry_type）+ 我针对哪个业务对象（source_id）"，
        不再要求页面让用户手工复制 workflow_id：

        * 显式给了 ``workflow_id`` → 选择（已有）或创建该流程，用于跨入口汇入同一条链；
        * 没给 → 按 ``(entry_type, source_id)`` 确定性派生。

        返回值带 ``created`` 与 ``derived`` 两个标志：页面据此如实显示"这是新开的链"
        还是"汇入了已有链"，而不是让用户猜自己刚才那一下到底做了什么。
        """
        entry = str(entry_type or "").strip()
        if entry not in dict(FlywheelRun.ENTRY_CHOICES):
            raise ValidationError(f"未知入口类型：{entry}")
        resolved = str(workflow_id or "").strip()
        derived = False
        if not resolved:
            resolved = cls.derive_workflow_id(entry, source_id)
            derived = True
        existed = FlywheelRun.objects.filter(
            project=project, workflow_id=resolved
        ).exists()
        merged_metadata = dict(metadata or {})
        if source_id:
            merged_metadata.setdefault("entry_source_id", str(source_id))
        run = cls.create(
            project=project, workflow_id=resolved, entry_type=entry, actor=actor,
            intent=intent, requirement_document_ids=requirement_document_ids,
            metadata=merged_metadata,
        )
        return {
            "run_id": str(run.pk),
            "project_id": run.project_id,
            "workflow_id": run.workflow_id,
            "entry_type": run.entry_type,
            "intent": run.intent,
            "status": run.status,
            "created": not existed,
            "derived": derived,
        }

    @classmethod
    def resolve_stage(cls, *, run: FlywheelRun, stage: str,
                      parent_output_ids=None) -> dict:
        stage = str(stage or "").strip()
        if stage not in ALL_WORKFLOW_STAGES:
            raise ValidationError(f"未知阶段：{stage}")

        parent_ids = list(dict.fromkeys(str(value) for value in (parent_output_ids or [])))
        parents = list(
            GenerationOutput.objects.filter(id__in=parent_ids).select_related("skill_version")
        )
        if len(parents) != len(parent_ids):
            raise ValidationError("存在无效的上游产出")
        for output in parents:
            protocol = (output.metadata or {}).get("protocol") or {}
            if output.project_id != run.project_id:
                raise ValidationError("上游产出不属于当前项目")
            if str(protocol.get("workflow_id") or "") != run.workflow_id:
                raise ValidationError("上游产出不属于当前流程")

        lock = (
            WorkflowSkillLock.objects
            .filter(project=run.project, workflow_id=run.workflow_id, stage=stage)
            .select_related("skill", "skill_version")
            .first()
        )
        return {
            "run_id": str(run.pk),
            "project_id": run.project_id,
            "workflow_id": run.workflow_id,
            "entry_type": run.entry_type,
            "intent": run.intent,
            "status": run.status,
            "stage": stage,
            "parent_output_ids": parent_ids,
            "managed": lock is not None and lock.skill_version_id is not None,
            "skill_id": str(lock.skill_id or "") if lock else "",
            "skill_version_id": str(lock.skill_version_id or "") if lock else "",
            "lock_id": str(lock.pk) if lock else "",
        }
