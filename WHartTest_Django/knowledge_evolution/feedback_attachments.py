"""阶段人工补充文件（T10 / §7.4）。

人在确认报告里**逐行**填的是"这一项留不留"，而"我还想补一份用例""这份需求
原文放在这"这类内容放不进表格——它们是一个完整文件。于是有了这条独立入口。

三个必须守住的边界：

1. **只当证据用**。上传不派生 Skill、不改 ``active`` 版本、不进金标集。
   本模块没有任何写 ``SkillVersion`` / ``EvaluationSuite`` 的路径，这是刻意的：
   把"上传顺便激活一下"写进来，人交一份参考附件就会改动生产 Skill。
2. **项目隔离是硬的**。文件绑定项目，重用的既有文件也必须属于本项目，
   跨项目一律拒绝；读取侧按项目成员判定，不是本项目成员拿不到文件名、
   正文和下载地址。
3. **幂等按绑定而不是按文件判**。同一份补充材料可以同时作为"人工补充产物"
   和"问题证据"，所以唯一性落在 (项目, 流程, 阶段, 用途, 哈希) 上。

物理文件复用 ``file_management.FileAsset``（它已经解决项目隔离、哈希、软删除、
下载与预览），同时登记一条 ``FileReference``——``cleanup_unreferenced_files``
删的是"零引用"的文件，不登记就会被当垃圾清掉。
"""
from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .capability_registry import WORKFLOW_STAGES
from .workflow_models import StageFeedbackAttachment

# ---------------------------------------------------------------- 用途真值（模块级）
#
# 与门禁状态一样写在模块级而不是类属性：别的模块要 `from .feedback_attachments
# import ATTACHMENT_PURPOSES`，类属性导不出来。

#: 确认后的完整产物（人把这一轮的结果整理成一份正式文件交回来）。
ATTACHMENT_PURPOSE_CONFIRMED = "confirmed_artifact"
#: 人工补充产物（新增的方案项、新增的用例）。
ATTACHMENT_PURPOSE_SUPPLEMENT = "manual_supplement"
#: 参考附件（需求原文、制度文件等，只作上下文）。
ATTACHMENT_PURPOSE_REFERENCE = "reference_attachment"
#: 问题证据（截图、日志、复现步骤）。
ATTACHMENT_PURPOSE_ISSUE = "issue_evidence"

ATTACHMENT_PURPOSES: tuple[str, ...] = (
    ATTACHMENT_PURPOSE_CONFIRMED,
    ATTACHMENT_PURPOSE_SUPPLEMENT,
    ATTACHMENT_PURPOSE_REFERENCE,
    ATTACHMENT_PURPOSE_ISSUE,
)

ATTACHMENT_PURPOSE_LABELS: dict[str, str] = {
    ATTACHMENT_PURPOSE_CONFIRMED: "确认后完整产物",
    ATTACHMENT_PURPOSE_SUPPLEMENT: "人工补充产物",
    ATTACHMENT_PURPOSE_REFERENCE: "参考附件",
    ATTACHMENT_PURPOSE_ISSUE: "问题证据",
}

#: 允许上传的阶段 = 新主链路四阶段。历史阶段（``risk_identification`` /
#: ``issue_tracking``）不开放：它们不在链路里流转，交上来的补充材料没有
#: 下游会去读，收下只是让人以为"已经进了流程"。
ATTACHMENT_STAGES: tuple[str, ...] = tuple(WORKFLOW_STAGES)

#: 审计轨迹保留条数。与派生物历史同量级：真正要追溯的是"最近改过什么"。
AUDIT_LIMIT = 30

#: 单文件上限单独设一档（比文件管理默认更严）：飞轮补充材料是**证据**，
#: 几百 MB 的整包交上来既过不了后续解析，也会把磁盘吃光。
ATTACHMENT_MAX_BYTES = 50 * 1024 * 1024


def purpose_label(purpose: str) -> str:
    return ATTACHMENT_PURPOSE_LABELS.get(str(purpose or ""), str(purpose or ""))


class StageAttachmentService:
    """上传、查询、退役阶段补充文件。"""

    # ------------------------------------------------------------------ 校验

    @staticmethod
    def _assert_stage(stage: str) -> str:
        stage = str(stage or "").strip()
        if stage not in ATTACHMENT_STAGES:
            raise ValidationError({
                "stage": f"该阶段不接受人工补充文件：{stage or '（空）'}"
            })
        return stage

    @staticmethod
    def _assert_purpose(purpose: str) -> str:
        purpose = str(purpose or "").strip()
        if purpose not in ATTACHMENT_PURPOSES:
            raise ValidationError({
                "purpose": (
                    f"未知用途：{purpose or '（空）'}；"
                    f"允许 {list(ATTACHMENT_PURPOSE_LABELS.values())}"
                )
            })
        return purpose

    @staticmethod
    def _assert_member(user, project_id) -> None:
        """与执行上下文解析用**同一条**规则。

        这里直接调用 ``StageExecutionContextService._assert_member`` 而不是抄一份：
        权限判定一旦有两份实现，迟早会在"superuser 算不算成员"这类细节上分叉，
        而分叉出来的那份通常更宽松。
        """
        from .operations import StageExecutionContextService

        StageExecutionContextService._assert_member(user, project_id)

    # ------------------------------------------------------------------ 上传

    @classmethod
    def upload(
        cls, *, project, stage: str, purpose: str, actor,
        upload=None, file_id=None, workflow_id: str = "",
        output=None, attempt=None, note: str = "",
    ) -> tuple[StageFeedbackAttachment, bool]:
        """登记一份人工补充文件，返回 ``(绑定, 是否新建绑定)``。

        第二个返回值说的是**绑定**，不是"有没有产生物理文件"：调用方（页面）
        用它决定回 201 还是 200，而"内容一样、只是换了个用途再交一次"对人是
        一次新提交，对文件则是同一份。

        ``upload`` 与 ``file_id`` 二选一：前者是本次新传的文件，后者是复用
        项目里已有的文件资产。两条路都要过同一套项目校验——"复用已有文件"
        不能变成绕过项目隔离的后门。
        """
        project_id = int(getattr(project, "pk", project))
        stage = cls._assert_stage(stage)
        purpose = cls._assert_purpose(purpose)
        cls._assert_member(actor, project_id)
        workflow_id = str(workflow_id or "")

        output = cls._resolve_output(project_id, workflow_id, stage, output)
        attempt = cls._resolve_attempt(project_id, stage, attempt)

        asset = cls._resolve_asset(project, actor, upload, file_id)
        sha256 = cls._asset_sha256(asset)

        with transaction.atomic():
            attachment = StageFeedbackAttachment.objects.filter(
                project_id=project_id, workflow_id=workflow_id, stage=stage,
                purpose=purpose, sha256=sha256,
            ).first()
            created = attachment is None
            if created:
                attachment = StageFeedbackAttachment.objects.create(
                    project_id=project_id, workflow_id=workflow_id, stage=stage,
                    purpose=purpose, output=output, attempt=attempt,
                    file=asset, sha256=sha256,
                    original_name=asset.original_name or "",
                    byte_size=asset.size or 0,
                    mime_type=asset.mime_type or "",
                    note=str(note or "")[:500],
                    metadata={"audit": []},
                    uploaded_by=actor if getattr(actor, "pk", None) else None,
                )
                cls._append_audit(
                    attachment, action="uploaded", actor=actor, note=str(note or ""),
                )
                attachment.save(update_fields=["metadata", "updated_at"])
            else:
                # 命中同一条绑定：可能是重复点上传，也可能是**退役之后又传回来**。
                # 后者要顺手复活 —— 人重新交一份就说明它现在有用，
                # 只回一个"已存在（已退役）"等于让人自己去翻列表找。
                reactivating = not attachment.is_active
                cls._append_audit(
                    attachment,
                    action="reactivated" if reactivating else "reuploaded",
                    actor=actor,
                    note=str(note or ""),
                )
                if reactivating:
                    attachment.retired_at = None
                    attachment.retired_by = None
                    attachment.retire_reason = ""
                attachment.save(update_fields=[
                    "retired_at", "retired_by", "retire_reason",
                    "metadata", "updated_at",
                ])

        cls._bind_file_reference(attachment, actor)
        return attachment, created

    # ------------------------------------------------------------------ 查询

    @classmethod
    def list_for(
        cls, *, project_id, stage: str = "", workflow_id: str = "",
        purpose: str = "", include_retired: bool = False, limit: int = 200,
    ):
        queryset = StageFeedbackAttachment.objects.filter(project_id=project_id)
        if stage:
            queryset = queryset.filter(stage=str(stage))
        if workflow_id:
            queryset = queryset.filter(workflow_id=str(workflow_id))
        if purpose:
            queryset = queryset.filter(purpose=str(purpose))
        if not include_retired:
            queryset = queryset.filter(retired_at__isnull=True)
        return queryset.select_related("file", "uploaded_by", "retired_by")[: max(1, int(limit))]

    @classmethod
    def view(cls, attachment: StageFeedbackAttachment) -> dict:
        """对外结构。下载/预览地址复用文件管理页的既有接口。

        ⚠️ 这个函数**只做形状转换，不做权限判定**：调用方必须先确认请求者
        是本项目成员。把它顺手做成"能返回 URL 就说明有权限"会让越权难查 ——
        因为失败的形态是"拿到了链接"，而不是"报了错"。
        """
        return {
            "id": attachment.pk,
            "purpose": attachment.purpose,
            "purpose_label": purpose_label(attachment.purpose),
            "project": attachment.project_id,
            "workflow_id": attachment.workflow_id,
            "stage": attachment.stage,
            "output_id": str(attachment.output_id or ""),
            "attempt_id": str(attachment.attempt_id or ""),
            "file_id": attachment.file_id,
            "filename": attachment.original_name,
            "byte_size": attachment.byte_size,
            "mime_type": attachment.mime_type,
            "sha256": attachment.sha256,
            "note": attachment.note,
            "active": attachment.is_active,
            "retired_at": attachment.retired_at.isoformat() if attachment.retired_at else None,
            "retired_by": (
                attachment.retired_by.username if attachment.retired_by_id else ""
            ),
            "retire_reason": attachment.retire_reason,
            "uploaded_by": (
                attachment.uploaded_by.username if attachment.uploaded_by_id else ""
            ),
            "uploaded_at": attachment.uploaded_at.isoformat(),
            "download_url": (
                f"/api/projects/{attachment.project_id}/files/{attachment.file_id}/download/"
            ),
            "preview_url": (
                f"/api/projects/{attachment.project_id}/files/{attachment.file_id}/preview/"
            ),
            "audit": attachment.history(),
        }

    # ------------------------------------------------------------------ 退役

    @classmethod
    def retire(cls, *, attachment: StageFeedbackAttachment, actor, reason: str = ""):
        """退役（软删除）。保留文件与审计，不做物理删除。

        为什么不真删：这份文件已经是某次人工确认的一部分，之后要回答
        "当时人交了什么、后来为什么撤"就只能靠它。真删掉等于把反馈链断在中间。
        """
        cls._assert_member(actor, attachment.project_id)
        if not attachment.is_active:
            return attachment
        attachment.retired_at = timezone.now()
        attachment.retired_by = actor if getattr(actor, "pk", None) else None
        attachment.retire_reason = str(reason or "")[:500]
        cls._append_audit(attachment, action="retired", actor=actor, note=str(reason or ""))
        attachment.save(update_fields=[
            "retired_at", "retired_by", "retire_reason", "metadata", "updated_at",
        ])
        return attachment

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _resolve_output(project_id, workflow_id: str, stage: str, output):
        from .operations import WorkflowGateService

        if output is not None:
            if int(getattr(output, "project_id", 0) or 0) != int(project_id):
                raise ValidationError({"output_id": "产出不属于当前项目"})
            protocol = (getattr(output, "metadata", None) or {}).get("protocol") or {}
            actual_stage = str(protocol.get("stage") or getattr(output, "task_type", ""))
            if actual_stage != stage:
                raise ValidationError({
                    "output_id": f"产出属于阶段 {actual_stage or '未知'}，与本次上传阶段 {stage} 不一致"
                })
            return output
        if not workflow_id:
            return None
        located, _gate = WorkflowGateService.locate_stage_output(
            project_id=project_id, workflow_id=workflow_id, stage=stage,
        )
        return located

    @staticmethod
    def _resolve_attempt(project_id, stage: str, attempt):
        if attempt is None:
            return None
        if int(getattr(attempt, "project_id", 0) or 0) != int(project_id):
            raise ValidationError({"attempt_id": "执行尝试不属于当前项目"})
        if str(getattr(attempt, "stage", "")) != stage:
            raise ValidationError({"attempt_id": "执行尝试的阶段与本次上传阶段不一致"})
        return attempt

    @classmethod
    def _resolve_asset(cls, project, actor, upload, file_id):
        """拿到文件资产：新传就建，``file_id`` 就**在同项目内**取。"""
        from file_management.models import FileAsset
        from file_management.services import calculate_sha256

        if upload is None and not file_id:
            raise ValidationError({"file": "必须提供 file（上传文件）或 file_id（复用已有文件）"})

        if upload is not None:
            size = int(getattr(upload, "size", 0) or 0)
            if size <= 0:
                raise ValidationError({"file": "文件内容为空"})
            if size > ATTACHMENT_MAX_BYTES:
                raise ValidationError({
                    "file": f"单文件不能超过 {ATTACHMENT_MAX_BYTES // 1024 // 1024}MB"
                })
            digest = calculate_sha256(upload)
            # 项目内同内容只留一份物理文件：补充材料经常"换个用途再交一次"，
            # 每次都存一份等于把同一份证据在磁盘上复制 N 遍，而它们永远同源。
            existing = FileAsset.objects.filter(
                project=project, sha256=digest, is_deleted=False,
            ).first()
            if existing is not None:
                return existing
            name = str(getattr(upload, "name", "") or "unnamed")
            extension = os.path.splitext(name)[1].lower()
            return FileAsset.objects.create(
                project=project,
                owner=actor if getattr(actor, "pk", None) else None,
                file=upload,
                original_name=name[:255],
                extension=extension[:32],
                mime_type=str(getattr(upload, "content_type", "") or "")[:255],
                size=size,
                sha256=digest,
            )

        # 复用既有文件：filter 里带 project 是关键的那一句，不带就等于给
        # "引用别人项目的文件"开了口子（`validate_file_ids` 也这么做，语义一致）。
        asset = FileAsset.objects.filter(
            pk=file_id, project=project, is_deleted=False,
        ).first()
        if asset is None:
            raise ValidationError({"file_id": "文件不存在、已删除或不属于当前项目"})
        return asset

    @staticmethod
    def _asset_sha256(asset) -> str:
        """取哈希；老文件没记哈希时**现算**。

        为什么不能"没哈希就跳过幂等"：跳过等于同一份文件每次上传都新插一条，
        而人以为自己只是重传了一次。
        """
        from file_management.models import FileAsset

        digest = str(getattr(asset, "sha256", "") or "")
        if digest:
            return digest
        import hashlib

        hasher = hashlib.sha256()
        with asset.file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        FileAsset.objects.filter(pk=asset.pk).update(sha256=digest)
        asset.sha256 = digest
        return digest

    @staticmethod
    def _bind_file_reference(attachment: StageFeedbackAttachment, actor) -> None:
        """登记文件引用，避免被"清理未引用文件"清掉。幂等（唯一约束兜底）。"""
        from file_management.models import FileReference

        FileReference.objects.get_or_create(
            file_id=attachment.file_id,
            ref_type=FileReference.REF_FEEDBACK_ATTACHMENT,
            ref_id=str(attachment.pk),
            defaults={
                "project_id": attachment.project_id,
                "created_by": actor if getattr(actor, "pk", None) else None,
            },
        )

    @staticmethod
    def _append_audit(attachment, *, action: str, actor, note: str = "") -> None:
        entries = list((attachment.metadata or {}).get("audit") or [])
        entries.append({
            "action": action,
            "at": timezone.now().isoformat(),
            "by": getattr(actor, "username", "") or "",
            "note": str(note or "")[:500],
        })
        attachment.metadata = {**(attachment.metadata or {}), "audit": entries[-AUDIT_LIMIT:]}


__all__ = [
    "ATTACHMENT_PURPOSES",
    "ATTACHMENT_PURPOSE_LABELS",
    "ATTACHMENT_STAGES",
    "ATTACHMENT_MAX_BYTES",
    "ATTACHMENT_PURPOSE_CONFIRMED",
    "ATTACHMENT_PURPOSE_SUPPLEMENT",
    "ATTACHMENT_PURPOSE_REFERENCE",
    "ATTACHMENT_PURPOSE_ISSUE",
    "StageAttachmentService",
    "purpose_label",
]
