from unittest.mock import patch
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from code_analysis.models import AnalysisTask, ProjectRepository
from projects.models import Project, ProjectMember
from knowledge.models import KnowledgeBase
from testcases.models import TestExecution, TestSuite
from rest_framework import status
from rest_framework.test import APIClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from orchestrator_integration.agent_loop_view import AgentLoopStreamAPIView

from .models import FeedbackEvent, GenerationOutput, RetrievalTrace
from .services import record_code_review_task, record_knowledge_query, record_test_execution


class KnowledgeEvolutionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="flywheel-user", password="pass")
        self.other_user = User.objects.create_user(username="other-user", password="pass")
        self.project = Project.objects.create(name="Flywheel Project", creator=self.user)
        self.other_project = Project.objects.create(name="Other Project", creator=self.other_user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")
        ProjectMember.objects.create(project=self.other_project, user=self.other_user, role="owner")
        self.knowledge_base = KnowledgeBase.objects.create(
            name="Product Knowledge", project=self.project, creator=self.user
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_trace(self):
        ids = record_knowledge_query(
            knowledge_base=self.knowledge_base,
            user=self.user,
            query="支付流程有哪些风险？",
            answer="需要检查幂等与回滚。",
            sources=[{
                "content": "这段原文不应进入轨迹候选集",
                "similarity_score": 0.91,
                "metadata": {"document_id": "doc-1", "chunk_index": 3, "source": "payment.md"},
                "fusion_detail": {"sources": ["dense", "sparse"]},
            }],
            retrieval_time=0.1,
            generation_time=0.2,
            total_time=0.3,
        )
        self.assertIsNotNone(ids)
        return ids

    def test_record_query_creates_trace_and_output_without_raw_chunk_content(self):
        trace_id, output_id = self.create_trace()

        trace = RetrievalTrace.objects.get(id=trace_id)
        output = GenerationOutput.objects.get(id=output_id)
        self.assertEqual(trace.channels["sparse"]["enabled"], True)
        self.assertEqual(trace.timings["total_ms"], 300)
        self.assertNotIn("content", trace.candidates[0])
        self.assertEqual(output.trace, trace)
        self.assertEqual(output.metadata["citation_count"], 1)

    @patch("knowledge_evolution.services.RetrievalTrace.objects.create", side_effect=RuntimeError("db unavailable"))
    def test_recording_failure_does_not_break_query(self, _mock_create):
        result = record_knowledge_query(
            knowledge_base=self.knowledge_base, user=self.user, query="q", answer="a",
            sources=[], retrieval_time=0, generation_time=0, total_time=0,
        )
        self.assertIsNone(result)

    def test_trace_api_is_project_scoped(self):
        trace_id, _ = self.create_trace()
        RetrievalTrace.objects.create(project=self.other_project, task_type="knowledge_query", query="secret")

        response = self.client.get("/api/knowledge-evolution/retrieval-traces/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        rows = payload.get("results", payload.get("data", payload))
        if isinstance(rows, dict):
            rows = rows.get("results", rows.get("data", []))
        self.assertEqual([str(row["id"]) for row in rows], [trace_id])

    def test_member_can_submit_feedback_and_non_member_cannot(self):
        trace_id, output_id = self.create_trace()
        response = self.client.post(
            "/api/knowledge-evolution/feedback/",
            {"output": output_id, "signal": "accepted", "idempotency_key": "accept-1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = FeedbackEvent.objects.get(idempotency_key="accept-1")
        self.assertEqual(event.project, self.project)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(str(event.trace_id), trace_id)

        self.client.force_authenticate(self.other_user)
        denied = self.client.post(
            "/api/knowledge-evolution/feedback/",
            {"trace": trace_id, "signal": "rejected", "idempotency_key": "reject-1"},
            format="json",
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_code_review_records_four_result_classes_and_counter_evidence(self):
        repository = ProjectRepository.objects.create(
            project=self.project, source_type="local_git", local_path="sample",
            name="Sample Repo", path_with_namespace="sample", default_branch="main",
        )
        task = AnalysisTask.objects.create(
            project=self.project, repository=repository, creator=self.user,
            executor=self.user, source_type="commits", base_sha="base", head_sha="head",
            status="completed", mode="standard", machine_coverage=100, ai_coverage=100,
            token_usage=321, completed_at=timezone.now(),
            change_report={
                "schema_version": 6,
                "summary": {
                    "changed_files": 2, "confirmed_count": 1,
                    "needs_confirmation_count": 1, "advisory_count": 1,
                },
                "findings": [
                    {"key": "confirmed", "file": "a.py", "severity": "high", "disposition": "confirmed"},
                    {"key": "pending", "file": "b.py", "severity": "medium", "disposition": "needs_confirmation"},
                    {"key": "advice", "file": "c.py", "severity": "low", "disposition": "advisory"},
                ],
            },
        )

        ids = record_code_review_task(task, [{
            "key": "rejected", "file": "d.py", "severity": "low",
            "reason": "调用方已做空值保护", "counter_evidence": "guard clause",
        }])

        trace = RetrievalTrace.objects.get(pk=ids[0])
        self.assertEqual(
            {item["disposition"] for item in trace.candidates},
            {"confirmed", "needs_confirmation", "advisory", "counter_evidence_rejected"},
        )
        self.assertEqual(trace.token_usage, 321)
        self.assertEqual(trace.channels["counter_evidence"]["rejected_count"], 1)
        self.assertNotIn("raw_diff", GenerationOutput.objects.get(pk=ids[1]).content)

    def test_test_execution_creates_objective_feedback(self):
        suite = TestSuite.objects.create(
            name="Regression", project=self.project, creator=self.user
        )
        execution = TestExecution.objects.create(
            suite=suite, executor=self.user, status="completed",
            total_count=3, passed_count=3, completed_at=timezone.now(),
            started_at=timezone.now(),
        )

        ids = record_test_execution(execution)

        self.assertIsNotNone(ids)
        event = FeedbackEvent.objects.get(idempotency_key=f"test-execution:{execution.pk}:completed")
        self.assertEqual(event.signal, "test_passed")
        self.assertEqual(str(event.trace_id), ids[0])

    def test_testcase_generation_agent_output_is_traceable(self):
        # 用例生成是全链路测试的第二段：平台自 T15 起强制"上一阶段门禁通过才能进入
        # 下一阶段"。所以这里必须先有一条已放行的方案阶段，否则 agent 产出会被链路
        # 门禁拦下、`publish_output` 返回 None——那是链路的正确行为，不是记录故障。
        # 用真实协议发一份方案产出再放行门禁，而不是直接捏一条门禁记录，
        # 是为了让这条用例同时覆盖"方案阶段产出 → 门禁 → 用例阶段产出"的真实顺序。
        from .protocol import ADAPTERS, publish_output
        from .workflow_models import WorkflowStageGate

        plan_envelope = ADAPTERS["test_plan_generation"].build(
            project=self.project, user=self.user, source_id="plan-for-generation",
            workflow_id="generation-session-1", input_summary="登录需求测试方案",
            output={"content": "方案正文"},
            extensions={"enforce_quality_gate": True},
        )
        self.assertIsNotNone(publish_output(plan_envelope))
        WorkflowStageGate.objects.filter(
            project=self.project, workflow_id="generation-session-1",
            stage="test_plan_generation",
        ).update(status="passed")

        request = SimpleNamespace(
            user=self.user, _flywheel_module_key="testcase_generation"
        )

        ids = async_to_sync(AgentLoopStreamAPIView()._record_module_output)(
            request=request,
            project=self.project,
            session_id="generation-session-1",
            user_message="根据登录需求生成测试用例",
            all_messages=[
                HumanMessage(content="生成用例"),
                AIMessage(content="", tool_calls=[{
                    "id": "save-1", "name": "save_testcases", "args": {"private": "secret"},
                }]),
                ToolMessage(content="saved 5", tool_call_id="save-1", name="save_testcases"),
                AIMessage(content="已生成并保存 5 条登录测试用例"),
            ],
            model_name="test-model",
            prompt_id=12,
            total_tokens=456,
            step_count=3,
            use_knowledge_base=True,
            knowledge_base_ids=[str(self.knowledge_base.id)],
        )

        trace = RetrievalTrace.objects.get(pk=ids[0])
        output = GenerationOutput.objects.get(pk=ids[1])
        self.assertEqual(trace.task_type, "testcase_generation")
        self.assertEqual(trace.token_usage, 456)
        self.assertTrue(trace.channels["knowledge"]["enabled"])
        self.assertEqual(output.model_version, "test-model")
        self.assertEqual(output.prompt_version, "12")
        tool_span = trace.spans.get(
            step_type="tool", tool_name="save_testcases",
            metadata__granularity="real_tool_call",
        )
        self.assertEqual(tool_span.status, "completed")
        self.assertTrue(tool_span.input_hash and tool_span.output_hash)
        self.assertNotIn("secret", str(tool_span.metadata))
