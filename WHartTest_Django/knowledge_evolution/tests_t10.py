"""T10：阶段人工补充文件。

这一层最容易出的两类错：

- **上传顺手改了生产**：交一份参考附件，某个 Skill 被"顺便"派生了候选版本。
  所以有一组用例专门断言上传前后 ``SkillVersion`` / 金标套件的数量不变。
- **跨项目读到了**："文件 id 猜对了就能下载"——因为下载地址是文件管理页的
  通用接口。判定必须发生在**绑定层**：拿到 URL 之前先问项目成员身份。
"""
from __future__ import annotations

import json
import tempfile
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution import feedback_attachments as fa
from knowledge_evolution.capability_registry import WORKFLOW_STAGES
from knowledge_evolution.feedback_attachments import (
    ATTACHMENT_PURPOSE_CONFIRMED,
    ATTACHMENT_PURPOSE_ISSUE,
    ATTACHMENT_PURPOSE_LABELS,
    ATTACHMENT_PURPOSE_REFERENCE,
    ATTACHMENT_PURPOSE_SUPPLEMENT,
    ATTACHMENT_PURPOSES,
    ATTACHMENT_STAGES,
    StageAttachmentService,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.workflow_models import StageFeedbackAttachment
from skills.models import SkillVersion

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t10-")

STAGE = "test_plan_generation"
CASE_STAGE = "testcase_generation"


def _file(name: str = "补充用例.xlsx", body: bytes = None) -> SimpleUploadedFile:
    if body is None:
        body = "补充用例正文".encode("utf-8")
    return SimpleUploadedFile(
        name, body,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# 取值真值
# ---------------------------------------------------------------------------


class AttachmentTruthTests(SimpleTestCase):
    def test_four_purposes_are_module_level(self):
        self.assertEqual(
            ATTACHMENT_PURPOSES,
            (
                ATTACHMENT_PURPOSE_CONFIRMED, ATTACHMENT_PURPOSE_SUPPLEMENT,
                ATTACHMENT_PURPOSE_REFERENCE, ATTACHMENT_PURPOSE_ISSUE,
            ),
        )
        self.assertEqual(len(ATTACHMENT_PURPOSE_LABELS), 4)
        self.assertEqual(ATTACHMENT_PURPOSE_LABELS[ATTACHMENT_PURPOSE_SUPPLEMENT], "人工补充产物")

    def test_only_main_chain_stages_accept_attachments(self):
        """历史阶段不开放：交上来的材料没有下游会读，收下等于假装进了流程。"""
        self.assertEqual(ATTACHMENT_STAGES, tuple(WORKFLOW_STAGES))
        self.assertNotIn("risk_identification", ATTACHMENT_STAGES)
        self.assertNotIn("issue_tracking", ATTACHMENT_STAGES)

    def test_purpose_label_falls_back_to_raw_value(self):
        self.assertEqual(fa.purpose_label("not_a_purpose"), "not_a_purpose")
        self.assertEqual(fa.purpose_label(""), "")


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AttachmentUploadTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t10")

    def _upload(self, **overrides):
        payload = {
            "project": self.project, "stage": STAGE,
            "purpose": ATTACHMENT_PURPOSE_SUPPLEMENT, "actor": self.lead,
            "upload": _file(), "workflow_id": "wf-t10",
        }
        payload.update(overrides)
        return StageAttachmentService.upload(**payload)

    def test_upload_records_binding_and_registers_file_reference(self):
        from file_management.models import FileAsset, FileReference

        attachment, created = self._upload(output=self.output)

        self.assertTrue(created)
        self.assertEqual(attachment.stage, STAGE)
        self.assertEqual(attachment.purpose, ATTACHMENT_PURPOSE_SUPPLEMENT)
        self.assertEqual(attachment.workflow_id, "wf-t10")
        self.assertEqual(attachment.output_id, self.output.pk)
        self.assertEqual(attachment.uploaded_by_id, self.lead.pk)
        self.assertEqual(len(attachment.sha256), 64)
        self.assertEqual(attachment.byte_size, len("补充用例正文".encode("utf-8")))
        self.assertTrue(attachment.is_active)
        self.assertEqual(attachment.history()[0]["action"], "uploaded")

        asset = FileAsset.objects.get(pk=attachment.file_id)
        self.assertEqual(asset.project_id, self.project.pk)
        # 引用必须登记：否则"清理未引用文件"会把人工证据当垃圾删掉。
        self.assertTrue(
            FileReference.objects.filter(
                file=asset, ref_type=FileReference.REF_FEEDBACK_ATTACHMENT,
                ref_id=str(attachment.pk),
            ).exists()
        )

    def test_same_content_is_idempotent_per_binding(self):
        first, created_first = self._upload()
        second, created_second = self._upload()

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            StageFeedbackAttachment.objects.filter(project=self.project).count(), 1,
        )
        # 幂等命中也要留痕：否则"我传了两次"和"只传过一次"在审计上分不出来。
        self.assertEqual(second.history()[0]["action"], "reuploaded")

    def test_same_file_under_another_purpose_is_a_new_binding(self):
        """同一份文件可以既是"人工补充产物"又是"问题证据"，按绑定判幂等。"""
        supplement, _ = self._upload()
        issue, created = self._upload(purpose=ATTACHMENT_PURPOSE_ISSUE)

        self.assertTrue(created)
        self.assertNotEqual(supplement.pk, issue.pk)
        self.assertEqual(supplement.file_id, issue.file_id)
        self.assertEqual(
            StageFeedbackAttachment.objects.filter(project=self.project).count(), 2,
        )

    def test_unknown_purpose_and_stage_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._upload(purpose="whatever")
        self.assertIn("purpose", ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            self._upload(stage="risk_identification")
        self.assertIn("stage", ctx.exception.message_dict)

    def test_missing_file_source_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._upload(upload=None)
        self.assertIn("file", ctx.exception.message_dict)

    def test_empty_file_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._upload(upload=_file(body=b""))
        self.assertIn("file", ctx.exception.message_dict)

    def test_oversized_file_is_rejected(self):
        with mock.patch.object(fa, "ATTACHMENT_MAX_BYTES", 8):
            with self.assertRaises(ValidationError) as ctx:
                self._upload(upload=_file(body=b"123456789"))
        self.assertIn("file", ctx.exception.message_dict)

    def test_non_member_cannot_upload(self):
        from django.contrib.auth.models import User
        from rest_framework.exceptions import PermissionDenied

        outsider = User.objects.create_user(username="t10-outsider", password="p")
        with self.assertRaises(PermissionDenied):
            self._upload(actor=outsider)

    # ---------------------------------------------------------- 项目隔离

    def test_reusing_file_from_another_project_is_rejected(self):
        from file_management.models import FileAsset

        foreign = FileAsset.objects.create(
            project=self.other_project, owner=self.lead, file=_file(),
            original_name="别项目文件.xlsx", size=10, sha256="a" * 64,
        )
        with self.assertRaises(ValidationError) as ctx:
            self._upload(upload=None, file_id=foreign.pk)
        self.assertIn("file_id", ctx.exception.message_dict)

    def test_reusing_file_in_same_project_is_allowed(self):
        mine, _ = self._upload()

        attachment, created = self._upload(
            upload=None, file_id=mine.file_id,
            purpose=ATTACHMENT_PURPOSE_REFERENCE,
        )

        self.assertTrue(created)
        self.assertEqual(attachment.file_id, mine.file_id)

    def test_output_from_another_project_is_rejected(self):
        foreign_output = self.make_output(
            task_type=STAGE, project=self.other_project, workflow_id="wf-other",
        )
        with self.assertRaises(ValidationError) as ctx:
            self._upload(output=foreign_output)
        self.assertIn("output_id", ctx.exception.message_dict)

    def test_output_stage_mismatch_is_rejected(self):
        other_stage = self.make_output(task_type=CASE_STAGE, workflow_id="wf-t10")
        with self.assertRaises(ValidationError) as ctx:
            self._upload(output=other_stage)
        self.assertIn("output_id", ctx.exception.message_dict)

    # ---------------------------------------------------------- 不产生副作用

    def test_upload_never_derives_or_activates_a_skill(self):
        """上传只进反馈：不派生版本、不新建套件、不改 Skill 开关。"""
        from knowledge_evolution.models import EvaluationSuite
        from skills.models import Skill

        skill, _version = self.make_skill_version(name="t10-plan", stage=STAGE)
        before_versions = SkillVersion.objects.count()
        before_suites = EvaluationSuite.objects.count()

        self._upload()

        self.assertEqual(SkillVersion.objects.count(), before_versions)
        self.assertEqual(EvaluationSuite.objects.count(), before_suites)
        self.assertTrue(Skill.objects.get(pk=skill.pk).is_active)
        self.assertEqual(skill.versions.count(), 1)
        self.assertEqual(
            StageFeedbackAttachment.objects.filter(project=self.project).count(), 1,
        )

    def test_upload_binds_attempt_of_same_stage(self):
        from knowledge_evolution.operations import StageExecutionAttemptService

        attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t10", stage=STAGE, actor=self.lead,
            idempotency_key="t10-attempt",
        )
        attachment, _ = self._upload(attempt=attempt)
        self.assertEqual(attachment.attempt_id, attempt.pk)

        mismatched, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t10", stage=CASE_STAGE, actor=self.lead,
            idempotency_key="t10-attempt-other",
        )
        with self.assertRaises(ValidationError) as ctx:
            self._upload(attempt=mismatched, purpose=ATTACHMENT_PURPOSE_ISSUE)
        self.assertIn("attempt_id", ctx.exception.message_dict)


# ---------------------------------------------------------------------------
# 退役与审计
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AttachmentRetireTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t10")
        self.attachment, _ = StageAttachmentService.upload(
            project=self.project, stage=STAGE, purpose=ATTACHMENT_PURPOSE_SUPPLEMENT,
            actor=self.lead, upload=_file(), workflow_id="wf-t10",
        )

    def test_retire_is_soft_and_keeps_file(self):
        from file_management.models import FileAsset

        StageAttachmentService.retire(
            attachment=self.attachment, actor=self.lead, reason="换成新版本",
        )
        self.attachment.refresh_from_db()

        self.assertFalse(self.attachment.is_active)
        self.assertEqual(self.attachment.retired_by_id, self.lead.pk)
        self.assertEqual(self.attachment.retire_reason, "换成新版本")
        # 物理文件与产出都还在：退役不等于抹掉证据。
        self.assertTrue(FileAsset.objects.filter(pk=self.attachment.file_id).exists())
        self.assertEqual(
            [entry["action"] for entry in self.attachment.history()],
            ["retired", "uploaded"],
        )

    def test_retire_is_idempotent(self):
        StageAttachmentService.retire(attachment=self.attachment, actor=self.lead, reason="一")
        StageAttachmentService.retire(attachment=self.attachment, actor=self.lead, reason="二")
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.retire_reason, "一")
        self.assertEqual(len(self.attachment.history()), 2)

    def test_non_member_cannot_retire(self):
        from django.contrib.auth.models import User
        from rest_framework.exceptions import PermissionDenied

        outsider = User.objects.create_user(username="t10-retire-outsider", password="p")
        with self.assertRaises(PermissionDenied):
            StageAttachmentService.retire(attachment=self.attachment, actor=outsider)

    def test_list_hides_retired_by_default(self):
        StageAttachmentService.retire(attachment=self.attachment, actor=self.lead)
        self.assertEqual(
            len(StageAttachmentService.list_for(project_id=self.project.pk)), 0,
        )
        self.assertEqual(
            len(StageAttachmentService.list_for(
                project_id=self.project.pk, include_retired=True,
            )), 1,
        )

    def test_reupload_after_retire_reactivates_same_binding(self):
        StageAttachmentService.retire(attachment=self.attachment, actor=self.lead, reason="误删")

        revived, created = StageAttachmentService.upload(
            project=self.project, stage=STAGE, purpose=ATTACHMENT_PURPOSE_SUPPLEMENT,
            actor=self.lead, upload=_file(), workflow_id="wf-t10",
        )

        self.assertFalse(created)
        self.assertEqual(revived.pk, self.attachment.pk)
        self.assertTrue(revived.is_active)
        self.assertEqual(revived.retire_reason, "")
        self.assertEqual(revived.history()[0]["action"], "reactivated")


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AttachmentApiTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t10-api")
        self.base = "/api/knowledge-evolution/operations/"

    def _params(self) -> dict:
        return {"project": self.project.pk, "workflow_id": "wf-t10-api", "stage": STAGE}

    def _post(self, payload: dict):
        return self.client.post(
            f"{self.base}workflow-stage-attachments/",
            {**self._params(), **payload}, format="multipart",
        )

    def test_catalog_returns_truth_from_backend(self):
        """选项真值由后端给：前端不自己抄一份，否则会出现"能选但提交 400"。"""
        from knowledge_evolution.operations import WorkflowGateService

        response = self.client.get(f"{self.base}workflow-stage-attachment-catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["max_bytes"], fa.ATTACHMENT_MAX_BYTES)
        self.assertEqual(
            [item["value"] for item in response.data["purposes"]], list(ATTACHMENT_PURPOSES),
        )
        self.assertEqual(
            [item["value"] for item in response.data["stages"]], list(ATTACHMENT_STAGES),
        )
        self.assertEqual(
            [item["label"] for item in response.data["stages"]],
            [WorkflowGateService.STAGE_LABELS[stage] for stage in ATTACHMENT_STAGES],
        )

    def test_upload_then_list_through_api(self):
        upload = self._post({
            "purpose": ATTACHMENT_PURPOSE_SUPPLEMENT,
            "file": _file(),
            "output_id": str(self.output.pk),
        })
        self.assertEqual(upload.status_code, 201)
        self.assertTrue(upload.data["created"])
        self.assertEqual(upload.data["purpose_label"], "人工补充产物")
        self.assertEqual(upload.data["output_id"], str(self.output.pk))
        self.assertIn(f"/files/{upload.data['file_id']}/download/", upload.data["download_url"])

        listed = self.client.get(f"{self.base}workflow-stage-attachments/", self._params())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(listed.data["results"][0]["filename"], "补充用例.xlsx")

        again = self._post({
            "purpose": ATTACHMENT_PURPOSE_SUPPLEMENT, "file": _file(),
        })
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.data["created"])

    def test_bad_purpose_returns_400_with_field(self):
        response = self._post({"purpose": "whatever", "file": _file()})
        self.assertEqual(response.status_code, 400)
        self.assertIn("purpose", json.dumps(response.data, ensure_ascii=False))

    def test_missing_project_is_rejected(self):
        response = self.client.get(f"{self.base}workflow-stage-attachments/")
        self.assertEqual(response.status_code, 400)

    def test_retire_through_api(self):
        upload = self._post({
            "purpose": ATTACHMENT_PURPOSE_REFERENCE, "file": _file(),
        })
        response = self.client.post(
            f"{self.base}workflow-stage-attachment-retire/",
            {"attachment_id": upload.data["id"], "reason": "不再需要"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["active"])
        self.assertEqual(response.data["retire_reason"], "不再需要")
        self.assertEqual(
            response.data["audit"][0]["action"], "retired",
        )

    # ---------------------------------------------------------- 越权

    def test_non_member_gets_403_and_no_filename(self):
        from django.contrib.auth.models import User

        self._post({"purpose": ATTACHMENT_PURPOSE_SUPPLEMENT, "file": _file()})

        outsider = User.objects.create_user(username="t10-api-outsider", password="p")
        self.client.force_authenticate(outsider)
        response = self.client.get(f"{self.base}workflow-stage-attachments/", self._params())
        self.assertEqual(response.status_code, 403)
        # 拒绝的形态必须是"没有内容"，而不是"报错但顺带给了文件名"。
        self.assertNotIn("补充用例.xlsx", json.dumps(response.data, ensure_ascii=False))

    def test_member_of_another_project_cannot_list(self):
        self.client.force_authenticate(self.executor)
        response = self.client.get(
            f"{self.base}workflow-stage-attachments/",
            {"project": self.other_project.pk, "stage": STAGE},
        )
        self.assertEqual(response.status_code, 403)

    def test_upload_without_member_is_403(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t10-api-outsider2", password="p")
        self.client.force_authenticate(outsider)
        response = self._post({"purpose": ATTACHMENT_PURPOSE_SUPPLEMENT, "file": _file()})
        self.assertEqual(response.status_code, 403)
