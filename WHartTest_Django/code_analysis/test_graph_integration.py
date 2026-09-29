from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .graph_client import CodeReviewGraphClient, GraphClientConfig, GraphUnavailable
from .services import _apply_verification_results, _repository_context_for_prompt, _summarize_graph_context


class GraphClientTests(SimpleTestCase):
    def setUp(self):
        self.client = CodeReviewGraphClient(GraphClientConfig(
            url="http://crg", token="token", prepare_timeout=10, query_timeout=10,
        ))
        self.task = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            repository_id=7, base_sha="a" * 40, head_sha="b" * 40,
        )

    @patch("code_analysis.graph_client.requests.post")
    def test_collect_context_keeps_every_changed_file(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "completed"}
        post.return_value = response
        self.client.collect_context(self.task, ["A.java", "B.java", "A.java"])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["changed_files"], ["A.java", "B.java"])

    @patch("code_analysis.graph_client.requests.post")
    def test_http_failure_degrades_to_graph_unavailable(self, post):
        response = Mock(status_code=500, text="failed")
        response.json.return_value = {"detail": {"code": "crg_failed"}}
        post.return_value = response
        with self.assertRaises(GraphUnavailable):
            self.client.prepare(self.task)

    @patch("code_analysis.graph_client.requests.post")
    def test_browse_bounds_depth_limit_and_keeps_filters(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "completed", "nodes": [], "edges": []}
        post.return_value = response
        self.client.browse(
            self.task, search="service", node_kinds=["Function"], edge_kinds=["CALLS"],
            center="42", depth=99, limit=999,
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["depth"], 3)
        self.assertEqual(payload["limit"], 300)
        self.assertEqual(payload["node_kinds"], ["Function"])


class GraphPromptTests(SimpleTestCase):
    def test_graph_evidence_is_first_and_bounded(self):
        context = {
            "parser": "tree-sitter",
            "graph_context": {
                "status": "completed",
                "affected_files": [f"F{i}.java" for i in range(100)],
                "callers": [{"source": f"caller-{i}"} for i in range(100)],
                "related_tests": [{"name": f"test-{i}"} for i in range(100)],
                "test_gaps": [{"name": f"gap-{i}"} for i in range(100)],
            },
            "items": [{"path": "A.java", "source": "x" * 10000}],
        }
        prompt = _repository_context_for_prompt(context)
        self.assertEqual(len(prompt["graph_context"]["affected_files"]), 40)
        self.assertEqual(len(prompt["graph_context"]["callers"]), 30)
        self.assertEqual(len(prompt["graph_context"]["related_tests"]), 30)
        self.assertEqual(len(prompt["graph_context"]["test_gaps"]), 30)
        self.assertEqual(len(prompt["items"][0]["source"]), 3000)

    def test_persisted_graph_context_is_summary_only(self):
        summary = _summarize_graph_context({
            "status": "completed",
            "affected_files": ["A.java"],
            "source_snippets": {"A.java": "large source"},
        })
        self.assertEqual(summary["counts"]["affected_files"], 1)
        self.assertNotIn("source_snippets", summary)

    def test_verification_result_exposes_support_and_counter_evidence(self):
        findings, rejected = _apply_verification_results(
            [{"key": "risk-1", "disposition": "needs_confirmation"}],
            [{
                "key": "risk-1", "verdict": "confirmed", "reason": "路径可达",
                "supporting_evidence": ["调用方直接进入该分支"],
                "counter_evidence": "未发现保护逻辑", "trigger_reachable": True,
            }],
        )
        self.assertFalse(rejected)
        self.assertEqual(findings[0]["verification_status"], "supported")
        self.assertTrue(findings[0]["target_commit_verified"])
        self.assertEqual(findings[0]["supporting_evidence"], ["调用方直接进入该分支"])
