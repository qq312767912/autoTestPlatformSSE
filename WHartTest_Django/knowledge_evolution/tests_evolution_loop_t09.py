"""R5③④：血缘的「内容来源」段与引用规范化。

本文件钉住四件事：

1. **``sources`` 永远在**，且四个键齐全。缺字段会让"没有引用"看起来像"功能没上"。
2. **两套引用写法都读得懂**（知识问答的 ``kb:`` 形态、四阶段的 ``evidence`` 形态），
   统一成一套结构 —— 前端只需要会渲染一种。
3. **引用里的节点要能跳**：从图谱节点 id 反查出 kind/label，
   并把 ``source_type`` 细化到 ``requirement`` / ``test_case``，否则页面不知道该跳到哪。
4. **节点被清理了也不许抹掉引用**：标 ``resolved=False`` 留着，
   抹掉会让人以为这份产出根本没引用任何东西。

判据来源：``specs/evolution-assisted-loop/design.md`` §3.3。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from django.test import override_settings

from knowledge_evolution.graph_models import GraphNode
from knowledge_evolution.lineage import OutputLineageService
from knowledge_evolution.models import GenerationOutput, RetrievalTrace
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT, SkillHubBaseTests

BASE = "/api/knowledge-evolution/generation-outputs/"

SOURCE_KEYS = {"channels", "citations", "graph_nodes", "skill_content"}


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LineageSourcesTests(SkillHubBaseTests):
    """``sources`` 段：结构、规范化、跳转信息。"""

    def _output_with(self, *, citations=None, channels=None, task_type="risk_identification",
                     skill_version=None, content="产出正文"):
        trace = RetrievalTrace.objects.create(
            project=self.project, user=self.executor, task_type=task_type,
            task_id=f"t-{task_type}", query="识别风险",
            channels=channels or {}, citations=citations or [],
        )
        return GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type=task_type,
            task_id=trace.task_id, content=content,
            output_hash=hashlib.sha256(content.encode()).hexdigest(),
            skill_version=skill_version,
        )

    def test_sources_always_present_with_all_four_keys(self):
        """没有引用时给空值，不给缺字段。"""
        trace = OutputLineageService.trace(self._output_with())

        self.assertIn("sources", trace)
        self.assertEqual(set(trace["sources"]), SOURCE_KEYS)
        self.assertEqual(trace["sources"]["citations"], [])
        self.assertEqual(trace["sources"]["graph_nodes"], [])
        self.assertEqual(trace["sources"]["channels"], {})
        # 没有 Skill 版本时也要是一个结构完整的空值。
        blank = trace["sources"]["skill_content"]
        self.assertEqual(blank["files"], [])
        self.assertEqual(blank["package_sha256"], "")

    def test_knowledge_query_citation_is_normalized_to_document(self):
        """知识问答形态 ``{citation_id, document_id, chunk_index, rank}`` → document。"""
        trace = OutputLineageService.trace(self._output_with(citations=[{
            "citation_id": "kb:1:doc-7:3", "document_id": "doc-7",
            "chunk_index": 3, "rank": 1,
        }]))

        citation = trace["sources"]["citations"][0]
        self.assertEqual(citation["source_type"], "document")
        self.assertEqual(citation["source_id"], "doc-7")
        self.assertEqual(citation["document_id"], "doc-7")
        self.assertEqual(citation["chunk_index"], 3)
        self.assertEqual(citation["citation_id"], "kb:1:doc-7:3")

    def test_graph_citation_resolves_kind_label_and_source_type(self):
        """引用图谱节点 → 反查出 kind/label，并把 source_type 细化。"""
        module = GraphNode.objects.create(
            project=self.project, node_type="requirement_module",
            external_id="req-login", name="登录与鉴权",
        )
        use_case = GraphNode.objects.create(
            project=self.project, node_type="test_case",
            external_id="tc-001", name="密码错误应锁定账户",
        )
        concept = GraphNode.objects.create(
            project=self.project, node_type="concept",
            external_id="risk-hot", name="高危操作",
        )
        trace = OutputLineageService.trace(self._output_with(citations=[
            {"node_id": "req-login"},
            {"node_id": str(use_case.id)},
            {"node_id": "risk-hot"},
        ]))

        citations = trace["sources"]["citations"]
        self.assertEqual(citations[0]["source_type"], "requirement")
        self.assertEqual(citations[0]["title"], "登录与鉴权")
        # 引用里给的是主键也要认。
        self.assertEqual(citations[1]["source_type"], "test_case")
        self.assertEqual(citations[2]["source_type"], "graph")

        nodes = {item["node_id"]: item for item in trace["sources"]["graph_nodes"]}
        self.assertEqual(len(nodes), 3)
        self.assertEqual(nodes["req-login"]["kind"], "requirement_module")
        self.assertEqual(nodes["req-login"]["label"], "登录与鉴权")
        self.assertTrue(nodes["req-login"]["resolved"])
        self.assertEqual(nodes[str(use_case.id)]["kind"], "test_case")
        self.assertEqual(nodes["risk-hot"]["kind"], "concept")

    def test_explicit_source_type_is_respected(self):
        """产出方显式声明了 ``source_type`` 时不要覆盖它。"""
        trace = OutputLineageService.trace(self._output_with(citations=[{
            "source_type": "test_case", "source_id": "TC-9", "title": "柜台开户",
        }]))

        citation = trace["sources"]["citations"][0]
        self.assertEqual(citation["source_type"], "test_case")
        self.assertEqual(citation["source_id"], "TC-9")
        self.assertEqual(citation["title"], "柜台开户")

    def test_string_citation_is_kept_not_dropped(self):
        """只有 id 的字符串引用要保留 —— 丢掉会让"引用数对不上"变成查不出来的差异。"""
        trace = OutputLineageService.trace(self._output_with(citations=["doc-999"]))

        citations = trace["sources"]["citations"]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["citation_id"], "doc-999")
        self.assertEqual(citations[0]["source_id"], "doc-999")
        self.assertEqual(citations[0]["source_type"], "")

    def test_missing_graph_node_is_reported_unresolved(self):
        """节点被清理（图谱重建）→ 仍列出并标 ``resolved=False``，不抹掉引用。"""
        trace = OutputLineageService.trace(self._output_with(citations=[
            {"node_id": "gone-node"},
        ]))

        nodes = trace["sources"]["graph_nodes"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["node_id"], "gone-node")
        self.assertFalse(nodes[0]["resolved"])

    def test_channels_are_passed_through_verbatim(self):
        """通道实况原样透传：它是"哪条通道真的跑了"的观测值，不该被二次解释。"""
        channels = {
            "dense": {"enabled": True},
            "graph": {"enabled": True, "hits": 6, "policy": "conditional"},
        }
        trace = OutputLineageService.trace(self._output_with(channels=channels))

        self.assertEqual(trace["sources"]["channels"]["graph"]["hits"], 6)
        self.assertEqual(trace["sources"]["channels"]["graph"]["policy"], "conditional")

    def test_skill_content_lists_files_with_real_hashes(self):
        """``skill_content`` 给文件清单 + 真实哈希，不落正文。"""
        skill, version = self.make_skill_version(name="risk-skill", stage="risk_identification")
        trace = OutputLineageService.trace(
            self._output_with(skill_version=version)
        )

        content = trace["sources"]["skill_content"]
        self.assertEqual(content["skill_version_id"], str(version.pk))
        self.assertEqual(content["package_sha256"], version.package_sha256)
        paths = {item["path"] for item in content["files"]}
        self.assertIn("SKILL.md", paths)

        entry = next(item for item in content["files"] if item["path"] == "SKILL.md")
        real = Path(version.get_full_path()) / "SKILL.md"
        self.assertEqual(entry["sha256"], hashlib.sha256(real.read_bytes()).hexdigest())
        self.assertEqual(entry["size"], real.stat().st_size)
        # 不落正文：清单里不该出现文件内容字段。
        self.assertNotIn("excerpt", entry)
        self.assertNotIn("content", entry)
        self.assertIsNotNone(skill)

    def test_api_exposes_lineage_and_scopes_by_project(self):
        """接口层：本项目可读，别项目的产出 404。"""
        from rest_framework.test import APIClient

        output = self._output_with(citations=[{"document_id": "doc-1", "rank": 1}])
        client = APIClient()
        client.force_authenticate(user=self.executor)

        response = client.get(f"{BASE}{output.pk}/lineage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["sources"]), SOURCE_KEYS)
        self.assertEqual(response.data["sources"]["citations"][0]["source_type"], "document")

        foreign = self.make_output(project=self.other_project, task_type="risk_identification")
        denied = client.get(f"{BASE}{foreign.pk}/lineage/")
        self.assertEqual(denied.status_code, 404)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class StageOutputSourcesTests(SkillHubBaseTests):
    """阶段产出上的内容来源：前端「查看结果」面板与血缘读的是同一份事实。"""

    def test_stage_output_response_exposes_artifact_and_history_shape(self):
        """阶段产出的 artifact 描述要能直接驱动按钮渲染。"""
        from rest_framework.test import APIClient

        from knowledge_evolution.operations import WORKFLOW_STAGE_ORDER, WorkflowGateService

        stage = WORKFLOW_STAGE_ORDER[0]
        _skill, version = self.make_skill_version(name="plan-skill", stage=stage)
        output = self.make_output(task_type=stage, skill_version=version, workflow_id="wf-src")
        output.metadata = {**(output.metadata or {}), "artifacts": []}
        output.save(update_fields=["metadata"])
        WorkflowGateService.register_output(output)

        client = APIClient()
        client.force_authenticate(user=self.executor)
        response = client.get(
            "/api/knowledge-evolution/operations/workflow-stage-output/",
            {"project": self.project.id, "workflow_id": "wf-src", "stage": stage},
        )

        self.assertEqual(response.status_code, 200)
        artifact = response.data["artifact"]
        self.assertTrue(artifact["available"])
        self.assertIn(artifact["source"], {"registered", "fallback"})
        # 采纳率历史是"版本对比"的读侧，形状固定为空数组也不要缺字段。
        self.assertEqual(response.data["acceptance_history"], [])
