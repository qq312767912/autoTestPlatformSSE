"""T09：最简人工确认报告闭环。

这一层最容易出的错是**把"没人看过"和"审核通过"混成同一件事**：

- 人工列初始必须为空，且空白**可以存草稿**但**不能进进化**；
- "修改后采纳"缺修改类型/内容、"删除"缺修改类型必须拒绝正式提交；
- 统计必须由服务端按明细重算 —— Excel 公式缓存可以被人一列粘贴搞坏，
  而错掉的统计会被下游当成"这一版质量"。
"""
from __future__ import annotations

import io
import json
import tempfile

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from openpyxl import load_workbook
from rest_framework.test import APIClient

from knowledge_evolution.models import FeedbackEvent
from knowledge_evolution.review_reports import (
    EDIT_CATEGORIES,
    REVIEW_REPORT_FILENAME,
    REVIEW_VERDICTS,
    SHEET_ITEM_ROWS,
    SHEET_TOTALS,
    ReviewReportFormatError,
    build_review_workbook,
    parse_review_workbook,
    validate_submission,
)
from knowledge_evolution.stage_outputs import TestPlanOutputAdapter, validate_stage_result
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.workflow_feedback import (
    REVIEW_REASON_CODE,
    REVIEW_STATE_DRAFT,
    REVIEW_STATE_SUBMITTED,
    ReviewSubmissionError,
    WorkflowStageReviewService,
)

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t09-")

STAGE = "test_plan_generation"


def _payload() -> dict:
    return {
        "schema_version": "stage-result/v1",
        "stage": STAGE,
        "status": "completed",
        "primary_artifacts": [{"name": "test_plan.xlsx", "kind": "xlsx"}],
        "requirements": [{"id": "REQ-12", "title": "非白名单账户校验"}],
        "items": [
            {
                "id": f"TP-{index:03d}",
                "title": f"方案项 {index}",
                "requirement_ids": ["REQ-12"],
                "evidence_ids": [f"EV-{index:03d}"],
                "scenario_type": "negative" if index % 2 else "positive",
                "priority": "P0" if index < 2 else "P1",
                "expected": f"预期结果 {index}",
            }
            for index in range(1, 4)
        ],
        "evidence": [
            {
                "id": f"EV-{index:03d}",
                "document_id": "doc-21",
                "document_version": "v3",
                "chunk_id": "chunk-108",
                "quote": "非白名单证券账户不得进入后续投票处理流程。",
            }
            for index in range(1, 4)
        ],
        "decision_summary": [{"decision": "按反向场景覆盖", "reason": "业务规则明确禁止"}],
    }


def _stage_result():
    validation = validate_stage_result(_payload(), expected_stage=STAGE)
    assert validation.ok, validation.as_dict()
    return TestPlanOutputAdapter().parse(validation.payload or {})


def _report_bytes() -> bytes:
    return build_review_workbook(_stage_result())


def _fill(payload: bytes, spec: dict[int, dict]) -> bytes:
    """按「数据行序号(0 基) → {列名: 值}」填人工列。"""
    workbook = load_workbook(io.BytesIO(payload))
    sheet = workbook[SHEET_ITEM_ROWS]
    headers = [cell.value for cell in sheet[1]]
    for row_index, values in spec.items():
        for column, value in values.items():
            sheet.cell(row=row_index + 2, column=headers.index(column) + 1, value=value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 读回与重算
# ---------------------------------------------------------------------------


class ReviewReportParsingTests(SimpleTestCase):
    def test_generated_report_round_trips(self):
        parsed = parse_review_workbook(_report_bytes())
        self.assertEqual(len(parsed["rows"]), 3)
        self.assertEqual(parsed["human_rows"], [])
        self.assertEqual(parsed["validation"]["blank_count"], 3)
        self.assertTrue(parsed["validation"]["ok"])
        self.assertEqual(parsed["statistics"]["原始项数"], 3)

    def test_rejects_empty_payload(self):
        with self.assertRaises(ReviewReportFormatError):
            parse_review_workbook(b"")

    def test_rejects_workbook_without_expected_sheet(self):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.title = "别的表"
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaises(ReviewReportFormatError) as ctx:
            parse_review_workbook(buffer.getvalue())
        self.assertIn(SHEET_ITEM_ROWS, str(ctx.exception))

    def test_statistics_are_recomputed_from_rows_not_formulas(self):
        """篡改「确认汇总」页不影响统计：服务端只信明细。"""
        filled = _fill(_report_bytes(), {
            0: {"人工结论": "采纳"},
            1: {"人工结论": "修改后采纳", "修改类型": "内容错误", "修改内容": "改成边界值"},
            2: {"人工结论": "删除", "修改类型": "重复或无效"},
        })
        workbook = load_workbook(io.BytesIO(filled))
        totals = workbook[SHEET_TOTALS]
        for row in totals.iter_rows(min_row=2):
            if str(row[0].value) == "采纳率":
                row[1].value = 0.99
        buffer = io.BytesIO()
        workbook.save(buffer)

        parsed = parse_review_workbook(buffer.getvalue())
        self.assertAlmostEqual(parsed["statistics"]["采纳率"], round(1 / 3, 4))
        self.assertAlmostEqual(parsed["statistics"]["有效保留率"], round(2 / 3, 4))
        self.assertAlmostEqual(parsed["statistics"]["删除率"], round(1 / 3, 4))
        self.assertAlmostEqual(parsed["statistics"]["确认完成率"], 1.0)

    def test_human_rows_only_include_touched_rows(self):
        filled = _fill(_report_bytes(), {1: {"人工结论": "采纳"}})
        parsed = parse_review_workbook(filled)
        self.assertEqual(len(parsed["human_rows"]), 1)
        self.assertEqual(parsed["human_rows"][0]["方案项 ID"], "TP-002")


class ReviewValidationTests(SimpleTestCase):
    def _rows(self, **verdicts):
        rows = [{"方案项 ID": f"TP-{index:03d}"} for index in range(1, len(verdicts) + 1)]
        for index, (_, values) in enumerate(sorted(verdicts.items())):
            rows[index].update(values)
        return rows

    def test_blank_verdicts_allowed_with_blank_count(self):
        result = validate_submission(self._rows(a={}, b={}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["blank_count"], 2)

    def test_adopt_with_changes_requires_category_and_content(self):
        result = validate_submission(self._rows(a={"人工结论": "修改后采纳"}))
        self.assertFalse(result["ok"])
        codes = {error["code"] for error in result["errors"]}
        self.assertEqual(codes, {"missing_required_field"})
        self.assertEqual(len(result["errors"]), 2)

    def test_delete_requires_category(self):
        result = validate_submission(self._rows(a={"人工结论": "删除"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["message"], "「删除」必须填写修改类型")

    def test_plain_adopt_requires_nothing_extra(self):
        result = validate_submission(self._rows(a={"人工结论": "采纳"}))
        self.assertTrue(result["ok"])

    def test_unknown_verdict_and_category_are_rejected(self):
        result = validate_submission(self._rows(
            a={"人工结论": "待确认"}, b={"人工结论": "采纳", "修改类型": "字太小"},
        ))
        self.assertFalse(result["ok"])
        codes = sorted(error["code"] for error in result["errors"])
        self.assertEqual(codes, ["unknown_edit_category", "unknown_verdict"])

    def test_verdict_and_category_truth_are_module_level(self):
        self.assertEqual(REVIEW_VERDICTS, ("采纳", "修改后采纳", "删除"))
        self.assertEqual(len(EDIT_CATEGORIES), 7)
        self.assertNotIn("待确认", REVIEW_VERDICTS)


# ---------------------------------------------------------------------------
# 草稿与正式提交
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ReviewServiceTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t09")

    def _draft(self, payload=None):
        return WorkflowStageReviewService.save_draft(
            output=self.output, stage=STAGE, data=payload or _report_bytes(),
            actor=self.lead, report_name="阶段确认报告.xlsx",
        )

    def _submit(self, payload=None):
        return WorkflowStageReviewService.submit(
            output=self.output, stage=STAGE, data=payload or _report_bytes(),
            actor=self.lead, report_name="阶段确认报告.xlsx",
        )

    def test_blank_draft_is_accepted_but_not_evolvable(self):
        result = self._draft()
        self.assertEqual(result["state"], REVIEW_STATE_DRAFT)
        self.assertFalse(result["evolvable"])
        self.assertEqual(result["validation"]["blank_count"], 3)
        event = FeedbackEvent.objects.get(pk=result["feedback_id"])
        self.assertEqual(event.reason_code, REVIEW_REASON_CODE)
        self.assertEqual(event.detail["state"], REVIEW_STATE_DRAFT)
        self.assertEqual(event.signal, "edited")
        # 绑定原始输出、trace 与 SkillVersion：版本化反馈的三要素。
        self.assertEqual(event.output_id, self.output.pk)
        self.assertEqual(event.trace_id, self.output.trace_id)
        self.assertEqual(event.skill_version_id, self.output.skill_version_id)

    def test_submit_rejects_incomplete_conditional_fields(self):
        """条件必填不满足 → 拒绝，且**一条反馈都不落**。

        这里断言的是 ``ReviewSubmissionError`` 而不是 Django ``ValidationError``：
        逐行错误必须原样带到页面（"第 3 行缺修改类型"），走 Django 那条路会把
        dict 展平成字符串，行号在半路就丢了。拒绝必须发生在写库之前 ——
        「提交被拒却留下一条草稿」会让人以为已经审过。
        """
        filled = _fill(_report_bytes(), {0: {"人工结论": "修改后采纳"}})
        with self.assertRaises(ReviewSubmissionError) as ctx:
            self._submit(filled)
        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertTrue(all(error.get("row") for error in ctx.exception.errors))
        self.assertFalse(FeedbackEvent.objects.filter(output=self.output).exists())

    def test_submit_records_versioned_feedback(self):
        filled = _fill(_report_bytes(), {
            0: {"人工结论": "采纳"},
            1: {"人工结论": "修改后采纳", "修改类型": "覆盖不足", "修改内容": "补边界用例"},
            2: {"人工结论": "删除", "修改类型": "重复或无效"},
        })
        result = self._submit(filled)
        self.assertEqual(result["state"], REVIEW_STATE_SUBMITTED)
        self.assertTrue(result["evolvable"])
        self.assertAlmostEqual(result["statistics"]["有效保留率"], round(2 / 3, 4))
        event = FeedbackEvent.objects.get(pk=result["feedback_id"])
        self.assertTrue(event.detail["evolvable"])
        # 指纹必须与"独立解析同一份文件"的结果一致，而不是自比自：
        # 它后面要用来判断"人是不是改了内容"，自比自等于没校验。
        self.assertEqual(
            event.detail["rows_fingerprint"], parse_review_workbook(filled)["fingerprint"],
        )
        self.assertEqual(event.detail["blank_count"], 0)
        self.assertEqual(event.detail["reviewed_count"], 3)

    def test_same_file_submission_is_idempotent(self):
        payload = _report_bytes()
        first = self._draft(payload)
        second = self._submit(payload)
        self.assertEqual(first["feedback_id"], second["feedback_id"])
        self.assertFalse(second["created"])
        self.assertEqual(FeedbackEvent.objects.filter(output=self.output).count(), 1)
        self.assertEqual(
            FeedbackEvent.objects.get(pk=second["feedback_id"]).detail["state"],
            REVIEW_STATE_SUBMITTED,
        )

    def test_modified_file_produces_new_feedback(self):
        first = self._submit()
        second = self._submit(_fill(_report_bytes(), {0: {"人工结论": "采纳"}}))
        self.assertNotEqual(first["feedback_id"], second["feedback_id"])
        self.assertEqual(
            FeedbackEvent.objects.filter(
                output=self.output, reason_code=REVIEW_REASON_CODE,
            ).count(), 2,
        )

    def test_latest_reflects_current_state(self):
        self.assertIsNone(WorkflowStageReviewService.latest(self.output))
        self._draft()
        self.assertEqual(
            WorkflowStageReviewService.latest(self.output)["state"], REVIEW_STATE_DRAFT,
        )
        self._submit()
        latest = WorkflowStageReviewService.latest(self.output)
        self.assertEqual(latest["state"], REVIEW_STATE_SUBMITTED)
        self.assertTrue(latest["evolvable"])

    def test_history_excludes_drafts(self):
        skill, _version = self.make_skill_version(name="t09-plan", stage=STAGE)
        self.output.skill_version = _version
        self.output.save(update_fields=["skill_version"])
        self._draft()
        self.assertEqual(WorkflowStageReviewService.history(skill), [])
        self._submit()
        history = WorkflowStageReviewService.history(skill)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["version"], _version.version)

    def test_missing_output_is_rejected(self):
        with self.assertRaises(ValidationError):
            WorkflowStageReviewService.save_draft(
                output=None, stage=STAGE, data=_report_bytes(), actor=self.lead,
            )

    def test_bad_format_is_reported_as_format_error(self):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.title = "无关表"
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaises(ValidationError) as ctx:
            self._submit(buffer.getvalue())
        self.assertIn(SHEET_ITEM_ROWS, str(ctx.exception))


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ReviewApiTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t09-api")
        self.output.metadata = {
            "protocol": {
                "schema_version": "platform-output/v1", "stage": STAGE,
                "workflow_id": "wf-t09-api", "parent_output_ids": [],
            },
            "stage_result_payload": _payload(),
        }
        self.output.save(update_fields=["metadata"])
        self.base = "/api/knowledge-evolution/operations/"

    def _params(self) -> dict:
        return {"project": self.project.pk, "workflow_id": "wf-t09-api", "stage": STAGE}

    def test_status_before_any_report(self):
        response = self.client.get(
            f"{self.base}workflow-stage-review-status/", self._params(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["report"])
        self.assertIsNone(response.data["latest_review"])
        self.assertTrue(response.data["structured_input_available"])

    def test_download_report_registers_derived_artifact(self):
        from knowledge_evolution import derived_artifacts as da
        from knowledge_evolution.workflow_models import StageDerivedArtifact

        response = self.client.get(
            f"{self.base}workflow-stage-review-report/", self._params(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(response["X-Artifact-Sha256"])
        self.assertEqual(
            len(response.content), 
            StageDerivedArtifact.objects.get(
                output=self.output, kind=da.KIND_REVIEW_REPORT,
            ).byte_size,
        )
        # 派生物不覆盖 Skill 业务主产物：产出正文与输出哈希都没变。
        self.output.refresh_from_db()
        self.assertEqual(self.output.content, "输出正文")

    def test_download_persists_artifact_hash_for_verification(self):
        self.client.get(f"{self.base}workflow-stage-review-report/", self._params())
        status = self.client.get(
            f"{self.base}workflow-stage-review-status/", self._params(),
        ).data
        self.assertEqual(status["report"]["filename"], REVIEW_REPORT_FILENAME)
        self.assertEqual(len(status["report"]["content_hash"]), 64)
        self.assertEqual(status["report"]["level"], "L3")

    def test_draft_then_submit_through_api(self):
        report = self.client.get(
            f"{self.base}workflow-stage-review-report/", self._params(),
        ).content
        upload = _fill(report, {0: {"人工结论": "采纳"}})

        draft = self.client.post(
            f"{self.base}workflow-stage-review-draft/",
            {**self._params(), "file": _as_upload(upload)}, format="multipart",
        )
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.data["state"], REVIEW_STATE_DRAFT)
        self.assertFalse(draft.data["evolvable"])

        submitted = self.client.post(
            f"{self.base}workflow-stage-review-submit/",
            {**self._params(), "file": _as_upload(upload)}, format="multipart",
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(submitted.data["state"], REVIEW_STATE_SUBMITTED)
        self.assertTrue(submitted.data["evolvable"])
        self.assertEqual(draft.data["feedback_id"], submitted.data["feedback_id"])

    def test_submit_with_validation_error_returns_row_level_details(self):
        report = self.client.get(
            f"{self.base}workflow-stage-review-report/", self._params(),
        ).content
        upload = _fill(report, {1: {"人工结论": "删除"}})
        response = self.client.post(
            f"{self.base}workflow-stage-review-submit/",
            {**self._params(), "file": _as_upload(upload)}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"]["count"], 1)
        self.assertEqual(
            response.data["detail"]["errors"][0]["row"], "TP-002",
        )

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            f"{self.base}workflow-stage-review-submit/", self._params(), format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_stage_is_rejected(self):
        response = self.client.get(
            f"{self.base}workflow-stage-review-status/",
            {"project": self.project.pk, "workflow_id": "wf-t09-api", "stage": "not_a_stage"},
        )
        self.assertEqual(response.status_code, 400)

    def test_no_output_is_reported_clearly(self):
        response = self.client.get(
            f"{self.base}workflow-stage-review-status/",
            {"project": self.project.pk, "workflow_id": "wf-absent", "stage": STAGE},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("暂无产出", str(response.data))

    def test_without_structured_payload_report_download_is_refused(self):
        self.output.metadata = {
            "protocol": {"stage": STAGE, "workflow_id": "wf-t09-api", "parent_output_ids": []},
        }
        self.output.save(update_fields=["metadata"])
        response = self.client.get(
            f"{self.base}workflow-stage-review-report/", self._params(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("结构化协议", str(response.data))


def _as_upload(payload: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(
        REVIEW_REPORT_FILENAME, payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
