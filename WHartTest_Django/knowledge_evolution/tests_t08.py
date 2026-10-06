"""T08：可实时查询的执行链路。

这一层最容易出的不是崩溃，而是**看起来正常的错数据**：

- Span 必须能在 ``running`` 状态下写与查。要求"等产出"会让失败的那一轮永远
  没有轨迹——而它恰恰是最需要看的一轮。
- 脱敏必须在**写**这一侧做。读侧漏一处就等于没脱敏。
- 原句正文按**原文所在知识库的项目**鉴权，不是按本次执行的项目的成员身份。
- 拿不到原文索引时是"验不了"（``declared``），不是"验错了"，两者处置完全不同。
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from knowledge_evolution.attribution import (
    BUSINESS_STEP_GROUPS,
    GROUP_LABELS,
    MAX_ERROR_SUMMARY,
    REDACTED_PLACEHOLDER,
    AttemptTraceService,
)
from knowledge_evolution.knowledge_models import KnowledgeAsset  # noqa: F401  (确保模型注册)
from knowledge_evolution.operations import StageExecutionAttemptService
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.trace_models import ExecutionSpan
from knowledge.models import Document, KnowledgeBase

STAGE = "test_plan_generation"
QUOTE = "非白名单证券账户不得进入后续投票处理流程。"


class RedactionTests(SimpleTestCase):
    def test_sensitive_keys_are_replaced_whole(self):
        payload = {
            "api_key": "sk-live-abcdefghijklmn",
            "X-Api-Key": "another",
            "Authorization": "Bearer xyz",
            "nested": {"access_token": "t", "keep": "值"},
            "list": [{"password": "p"}],
        }
        cleaned = AttemptTraceService.redact_mapping(payload)
        self.assertEqual(cleaned["api_key"], REDACTED_PLACEHOLDER)
        self.assertEqual(cleaned["X-Api-Key"], REDACTED_PLACEHOLDER)
        self.assertEqual(cleaned["Authorization"], REDACTED_PLACEHOLDER)
        self.assertEqual(cleaned["nested"]["access_token"], REDACTED_PLACEHOLDER)
        self.assertEqual(cleaned["nested"]["keep"], "值")
        self.assertEqual(cleaned["list"][0]["password"], REDACTED_PLACEHOLDER)

    def test_credentials_inside_free_text_are_masked(self):
        text = "调用失败：Authorization: Bearer abc.def-ghi 被拒；key=sk-1234567890abcd"
        cleaned = AttemptTraceService.redact_text(text)
        self.assertNotIn("abc.def-ghi", cleaned)
        self.assertNotIn("sk-1234567890abcd", cleaned)
        self.assertIn("***", cleaned)

    def test_error_summary_is_truncated(self):
        long_text = "错误" * 400
        limited = AttemptTraceService.limit_error(long_text)
        self.assertEqual(len(limited), MAX_ERROR_SUMMARY + 1)
        self.assertTrue(limited.endswith("…"))

    def test_error_summary_masks_before_truncating(self):
        limited = AttemptTraceService.limit_error("token=abcd1234efgh 出错")
        self.assertNotIn("abcd1234efgh", limited)


class TraceRecordingTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t08", stage=STAGE,
            actor=self.lead, idempotency_key="t08-1",
        )

    def test_span_can_be_recorded_before_any_output_exists(self):
        span = AttemptTraceService.record_span(
            attempt=self.attempt, step_type="retrieval", status="running",
            evidence=[{"id": "EV-1", "document_id": "doc-1", "quote": QUOTE}],
            metadata={"knowledge_base_ids": ["kb-1"], "api_key": "sk-secret-value"},
        )
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, "dispatched")
        self.assertIsNone(self.attempt.output_id)
        self.assertEqual(span.status, "running")
        self.assertEqual(span.workflow_id, "wf-t08")
        self.assertEqual(span.stage, STAGE)
        # 写入即脱敏：读侧漏一处就等于没脱敏。
        self.assertEqual(span.metadata["api_key"], REDACTED_PLACEHOLDER)
        self.assertEqual(span.metadata["knowledge_base_ids"], ["kb-1"])

    def test_sequence_auto_increments(self):
        first = AttemptTraceService.record_span(attempt=self.attempt, step_type="intent")
        second = AttemptTraceService.record_span(attempt=self.attempt, step_type="tool")
        third = AttemptTraceService.record_span(attempt=self.attempt, step_type="validation")
        self.assertEqual([first.sequence, second.sequence, third.sequence], [1, 2, 3])

    def test_explicit_sequence_must_be_integer(self):
        with self.assertRaises(ValueError):
            AttemptTraceService.record_span(
                attempt=self.attempt, step_type="tool", sequence="第二",
            )

    def test_evidence_must_be_list(self):
        span = AttemptTraceService.record_span(
            attempt=self.attempt, step_type="tool", evidence={"id": "EV-1"},
        )
        self.assertEqual(span.evidence, [])

    def test_unknown_fields_are_dropped(self):
        span = AttemptTraceService.record_span(
            attempt=self.attempt, step_type="tool", totally_unknown="x",
        )
        self.assertFalse(hasattr(span, "totally_unknown"))

    def test_trace_is_reused_per_attempt_and_isolated_between_attempts(self):
        first = AttemptTraceService.trace_for_attempt(self.attempt)
        again = AttemptTraceService.trace_for_attempt(self.attempt)
        self.assertEqual(first.pk, again.pk)
        other, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t08", stage=STAGE,
            actor=self.lead, idempotency_key="t08-2",
        )
        self.assertNotEqual(AttemptTraceService.trace_for_attempt(other).pk, first.pk)


class TraceSummaryTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t08", stage=STAGE,
            actor=self.lead, idempotency_key="t08-summary",
        )
        StageExecutionAttemptService.mark_running(self.attempt, session_id="sess-1")

    def _record(self, step_type, status="completed", **kwargs):
        return AttemptTraceService.record_span(
            attempt=self.attempt, step_type=step_type, status=status, **kwargs,
        )

    def test_business_groups_and_labels(self):
        self._record("intent")
        self._record("planning")
        self._record("retrieval", evidence=[{"id": "EV-1"}, {"id": "EV-2"}])
        self._record("graph_retrieval")
        self._record("tool", tool_name="search_knowledge", latency_ms=120)
        self._record("tool", tool_name="export_xlsx", latency_ms=80)
        self._record("validation", status="failed", error_message="方案项缺需求关联")

        summary = AttemptTraceService.summary(self.attempt, user=self.lead)
        self.assertEqual(summary["groups"]["input"]["count"], 2)
        self.assertEqual(summary["groups"]["retrieval"]["count"], 2)
        self.assertEqual(summary["groups"]["retrieval"]["evidence_count"], 2)
        self.assertEqual(summary["groups"]["tool"]["count"], 2)
        self.assertEqual(summary["groups"]["tool"]["names"], ["export_xlsx", "search_knowledge"])
        self.assertEqual(summary["groups"]["tool"]["latency_ms"], 200)
        self.assertEqual(summary["groups"]["validation"]["failed"], 1)
        self.assertEqual(summary["steps"]["total"], 7)
        self.assertEqual(summary["steps"]["failed"], 1)
        # 错误码留空（Span 没报），但失败**位置**要说清楚。
        self.assertEqual(summary["failure"]["error_code"], "")
        self.assertEqual(summary["failure"]["step_type"], "validation")
        self.assertEqual(summary["failure"]["group"], "validation")
        self.assertTrue(summary["can_see_quotes"])
        # 分组名必须都在标签表里，否则页面会出现空白分组标题。
        for name in BUSINESS_STEP_GROUPS:
            self.assertIn(name, GROUP_LABELS)
            self.assertIn(name, summary["groups"])

    def test_running_attempt_is_reported_as_in_flight(self):
        self._record("tool", status="running")
        summary = AttemptTraceService.summary(self.attempt, user=self.lead)
        self.assertTrue(summary["in_flight"])
        self.assertEqual(summary["steps"]["running"], 1)

    def test_failed_attempt_without_output_is_still_diagnosable(self):
        """无正式产出的失败任务也必须有轨迹可查（T08 验收第 2 条）。"""
        self._record("tool", status="failed", tool_name="export_xlsx",
                     error_message="token=abcd1234efgh 导出被拒")
        StageExecutionAttemptService.fail(
            self.attempt, error_code="ToolError", error_summary="导出工具失败：令牌无效",
        )
        summary = AttemptTraceService.summary(self.attempt, user=self.lead)
        events = AttemptTraceService.events(self.attempt, user=self.lead)
        spans = AttemptTraceService.spans(self.attempt, user=self.lead)

        self.assertIsNone(self.attempt.output_id)
        self.assertFalse(summary["in_flight"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failure"]["count"], 1)
        self.assertIn("令牌无效", summary["failure"]["error_summary"])
        self.assertEqual(len(spans), 1)
        self.assertNotIn("abcd1234efgh", spans[0]["error_summary"])
        kinds = [event["kind"] for event in events]
        self.assertIn("attempt_created", kinds)
        self.assertIn("attempt_finished", kinds)
        self.assertEqual(events[0]["kind"], "attempt_created")
        self.assertEqual(events[-1]["kind"], "attempt_finished")

    def test_span_payload_uses_field_whitelist(self):
        self._record(
            "tool", tool_name="search_knowledge", input_hash="a" * 64,
            metadata={"nested": {"secret_token": "x"}, "keep": "y"},
        )
        span = AttemptTraceService.spans(self.attempt, user=self.lead)[0]
        self.assertEqual(
            set(span) - {"error_summary", "metadata", "evidence", "group"},
            set(AttemptTraceService.SAFE_SPAN_FIELDS),
        )
        self.assertEqual(span["metadata"]["nested"]["secret_token"], REDACTED_PLACEHOLDER)
        self.assertEqual(span["tool_name"], "search_knowledge")


class QuotePermissionTests(SkillHubBaseTests):
    """原句正文按**原文所在知识库的项目**鉴权，而不是本次执行的项目。"""

    def setUp(self):
        super().setUp()
        self.attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t08", stage=STAGE,
            actor=self.lead, idempotency_key="t08-quote",
        )
        self.other_kb = KnowledgeBase.objects.create(
            name="他项目知识库", project=self.other_project, creator=self.lead,
        )
        self.other_doc = Document.objects.create(
            knowledge_base=self.other_kb, title="他项目规则", document_type="txt",
        )
        self.own_kb = KnowledgeBase.objects.create(
            name="本项目知识库", project=self.project, creator=self.lead,
        )
        self.own_doc = Document.objects.create(
            knowledge_base=self.own_kb, title="本项目规则", document_type="txt",
        )
        AttemptTraceService.record_span(
            attempt=self.attempt, step_type="retrieval",
            evidence=[
                {"id": "EV-OWN", "document_id": str(self.own_doc.pk), "quote": QUOTE},
                {"id": "EV-OTHER", "document_id": str(self.other_doc.pk), "quote": QUOTE},
                {"id": "EV-MISSING", "document_id": "00000000-0000-0000-0000-000000000000",
                 "quote": QUOTE},
            ],
        )

    def _quotes_for(self, user):
        span = AttemptTraceService.spans(self.attempt, user=user)[0]
        return {
            item["id"]: item for item in span["evidence"]
        }

    def test_project_member_sees_own_project_quote_only(self):
        items = self._quotes_for(self.executor)
        self.assertEqual(items["EV-OWN"]["quote"], QUOTE)
        self.assertFalse(items["EV-OWN"]["masked"])
        self.assertEqual(items["EV-OTHER"]["quote"], "")
        self.assertTrue(items["EV-OTHER"]["masked"])
        self.assertEqual(items["EV-OTHER"]["mask_reason"], "no_document_permission")
        # 定位信息仍保留：没有原句也能判断"指向哪份文档的哪一段"。
        self.assertEqual(items["EV-OTHER"]["document_id"], str(self.other_doc.pk))

    def test_unresolvable_document_is_denied(self):
        items = self._quotes_for(self.lead)
        self.assertEqual(items["EV-MISSING"]["quote"], "")
        self.assertTrue(items["EV-MISSING"]["masked"])

    def test_superuser_sees_cross_project_quote(self):
        boss = User.objects.create_superuser(username="t08-root", password="p")
        self.assertEqual(self._quotes_for(boss)["EV-OTHER"]["quote"], QUOTE)

    def test_non_member_sees_nothing(self):
        outsider = User.objects.create_user(username="t08-outsider", password="p")
        items = self._quotes_for(outsider)
        for item in items.values():
            self.assertEqual(item["quote"], "")
            self.assertTrue(item["masked"])

    def test_can_see_quotes_is_false_for_non_member(self):
        outsider = User.objects.create_user(username="t08-outsider2", password="p")
        self.assertFalse(AttemptTraceService.can_see_quotes(outsider, self.project.pk))
        self.assertTrue(AttemptTraceService.can_see_quotes(self.executor, self.project.pk))


class TraceApiTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.attempt, _ = StageExecutionAttemptService.dispatch(
            project=self.project, workflow_id="wf-t08", stage=STAGE,
            actor=self.lead, idempotency_key="t08-api",
        )
        self.base = f"/api/knowledge-evolution/stage-attempts/{self.attempt.pk}/"

    def test_trace_endpoint_returns_summary_events_and_spans(self):
        response = self.client.get(f"{self.base}trace/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"summary", "events", "spans"})
        self.assertEqual(response.data["summary"]["attempt_id"], str(self.attempt.pk))

    def test_recording_span_makes_it_visible_immediately(self):
        created = self.client.post(
            f"{self.base}trace-span/",
            {"step_type": "tool", "tool_name": "search_knowledge", "status": "running",
             "metadata": {"api_key": "sk-should-not-leak"}},
            format="json",
        )
        self.assertEqual(created.status_code, 201)

        response = self.client.get(f"{self.base}trace-spans/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["spans"]), 1)
        self.assertEqual(response.data["spans"][0]["status"], "running")
        self.assertEqual(
            response.data["spans"][0]["metadata"]["api_key"], REDACTED_PLACEHOLDER,
        )
        self.assertEqual(ExecutionSpan.objects.count(), 1)

    def test_span_without_step_type_is_rejected(self):
        response = self.client.post(f"{self.base}trace-span/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_non_member_cannot_read_or_write(self):
        outsider = User.objects.create_user(username="t08-api-outsider", password="p")
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(f"{self.base}trace/").status_code, 404)
        self.assertEqual(
            self.client.post(f"{self.base}trace-span/", {"step_type": "tool"}, format="json").status_code,
            404,
        )
