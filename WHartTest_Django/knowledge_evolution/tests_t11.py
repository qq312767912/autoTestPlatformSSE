"""T11：差异、证据反查与人工确认归因。

这一层最容易出的三类错：

- **假差异变成假归因**：人工只是把「REQ-02、REQ-01」调了个顺序，就被算成一次
  内容修改，于是"模型写错了"的归因凭空出现。所以有一组用例专门钉住顺序无关。
- **"知识在、没被用上"被误判成"知识缺失"**：两者在产出上长得一样，区别只在
  图谱里"召回了但没被引用"那条状态。归因必须先读它，才谈得上区分。
- **环境故障被写成 Skill 补丁**：超时、配额、网络问题改 ``SKILL.md`` 一点用没有，
  还会在下一轮评测里表现为"改了也没用"，把真正的环境问题掩盖掉。
"""
from __future__ import annotations

import tempfile
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution import stage_diff as sd
from knowledge_evolution.attribution import (
    EDIT_CATEGORY_RULES,
    AttributionService,
    HumanEditAttributionService,
    SpanRecorder,
    assert_usable_for_content_patch,
)
from knowledge_evolution.evidence_graph import REVERSE_CHAIN_ORDER, reverse_trace
from knowledge_evolution.feedback_attachments import (
    ATTACHMENT_PURPOSE_CONFIRMED,
    ATTACHMENT_PURPOSE_ISSUE,
    ATTACHMENT_PURPOSE_REFERENCE,
    ATTACHMENT_PURPOSE_SUPPLEMENT,
)
from knowledge_evolution.models import FeedbackEvent
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.workflow_feedback import REVIEW_REASON_CODE

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t11-")

STAGE = "test_plan_generation"


# --------------------------------------------------------------------------- 夹具


def _items() -> list[dict]:
    return [{
        "id": "TP-01", "title": "验证投票接口", "module": "投票",
        "scenario_type": "正常", "priority": "高", "expected": "返回 200",
        "requirement_ids": ["REQ-01"], "evidence_ids": ["EV-01"],
    }]


def _stage_result(items=None):
    return SimpleNamespace(items=_items() if items is None else items)


def _payload() -> dict:
    return {
        "schema_version": "stage-result/v1", "stage": STAGE, "status": "completed",
        "primary_artifacts": [{"name": "测试方案.xlsx"}],
        "items": _items(),
        "evidence": [{
            "id": "EV-01", "document_id": "DOC-1", "quote": "投票接口须返回 200",
        }],
        "requirements": [{"id": "REQ-01", "title": "投票接口"}],
    }


def _row(**overrides) -> dict:
    """一行确认报告。默认**不含人工列**——那代表"没审"，不是"审完没意见"。"""
    row = {
        "方案项 ID": "TP-01", "方案项标题": "验证投票接口", "模块": "投票",
        "场景类型": "正常", "优先级": "高", "可判定预期": "返回 200",
        "需求": "REQ-01", "证据": "EV-01",
    }
    row.update(overrides)
    return row


def _attachment(*, purpose, retired=False, pk=1):
    return SimpleNamespace(
        pk=pk, purpose=purpose, stage=STAGE, original_name="补充用例.xlsx",
        sha256="a" * 16, retired_at="2026-10-05T00:00:00Z" if retired else None,
        uploaded_by=SimpleNamespace(username="t09-lead"),
    )


def _graph() -> dict:
    nodes = [
        {"id": "humanedit:TP-01", "type": "HumanEdit", "key": "TP-01", "verdict": "修改后采纳"},
        {"id": "planitem:TP-01", "type": "PlanItem", "key": "TP-01", "title": "验证投票接口"},
        {"id": "decision:TP-01:priority", "type": "DecisionRecord", "key": "priority"},
        {"id": "quote:EV-01", "type": "EvidenceQuote", "key": "EV-01"},
        {"id": "chunk:KN-01", "type": "KnowledgeChunk", "key": "KN-01"},
        {"id": "req:REQ-01", "type": "Requirement", "key": "REQ-01"},
        {"id": "step:1", "type": "AgentStep", "key": "plan"},
        {"id": "tool:1", "type": "ToolCall", "key": "knowledge_search"},
        {"id": "skillrule:SKILL.md#0", "type": "SkillRule", "key": "SKILL.md#0"},
    ]
    edges = [
        {"source": "humanedit:TP-01", "target": "planitem:TP-01", "type": "MODIFIED_TO"},
        {"source": "planitem:TP-01", "target": "decision:TP-01:priority", "type": "BASED_ON"},
        {"source": "decision:TP-01:priority", "target": "quote:EV-01", "type": "CITES"},
        {"source": "quote:EV-01", "target": "chunk:KN-01", "type": "FROM"},
        {"source": "chunk:KN-01", "target": "req:REQ-01", "type": "COVERS"},
        {"source": "planitem:TP-01", "target": "step:1", "type": "GENERATED_BY"},
        {"source": "step:1", "target": "tool:1", "type": "CALLED"},
        {"source": "step:1", "target": "skillrule:SKILL.md#0", "type": "GENERATED_BY"},
    ]
    return {"nodes": nodes, "edges": edges, "evidence_status": {}}


# --------------------------------------------------------------- 差异：字段级


class StageDiffFieldTests(SimpleTestCase):
    def test_ref_order_is_not_a_change(self):
        """顺序调整不是内容修改：按字符串比会造出一批假差异，假差异变假归因。"""
        self.assertEqual(sd.normalize_refs("REQ-02、REQ-01"), ["REQ-01", "REQ-02"])
        self.assertEqual(sd.normalize_refs("REQ-01,REQ-02"), ["REQ-01", "REQ-02"])
        item = {**_items()[0], "requirement_ids": ["REQ-01", "REQ-02"]}
        self.assertEqual(sd.field_diffs(item, _row(需求="REQ-02、REQ-01")), [])

    def test_list_field_marks_itself(self):
        diffs = sd.field_diffs(_items()[0], _row(需求="REQ-01、REQ-02"))
        entry = next(item for item in diffs if item["field"] == "requirement_ids")
        self.assertTrue(entry["list_field"])
        self.assertEqual(entry["before"], ["REQ-01"])
        self.assertEqual(entry["after"], ["REQ-01", "REQ-02"])

    def test_direction_is_platform_then_human(self):
        """``before`` 恒为平台产出、``after`` 恒为人工值：方向反了，页面会显示成
        "平台改了他填的东西"。"""
        diffs = sd.field_diffs(_items()[0], _row(优先级="中"))
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["field"], "priority")
        self.assertEqual(diffs[0]["before"], "高")
        self.assertEqual(diffs[0]["after"], "中")

    def test_missing_column_is_not_a_clearing(self):
        """旧版报告没有这一列 ≠ 人清空了它。"""
        row = _row()
        row.pop("优先级")
        self.assertEqual(sd.field_diffs(_items()[0], row), [])

    def test_classify_maps_verdicts(self):
        self.assertEqual(sd.classify_item({"人工结论": "采纳"}, diffs=[]), ["adopted"])
        self.assertEqual(sd.classify_item({"人工结论": "修改后采纳"}, diffs=[]), ["modified"])
        self.assertEqual(sd.classify_item({"人工结论": "删除"}, diffs=[]), ["removed"])

    def test_classify_maps_edit_categories(self):
        self.assertIn(
            "evidence_error", sd.classify_item({"修改类型": "证据错误"}, diffs=[]),
        )
        self.assertIn(
            "requirement_gap", sd.classify_item({"修改类型": "覆盖不足"}, diffs=[]),
        )
        self.assertIn(
            "evidence_error", sd.classify_item({"证据状态": "无证据"}, diffs=[]),
        )

    def test_field_change_without_verdict_still_counts(self):
        """草稿阶段改了字段但没填结论，差异也必须可见。"""
        diffs = sd.field_diffs(_items()[0], _row(优先级="中"))
        self.assertIn("modified", sd.classify_item({"优先级": "中"}, diffs=diffs))


class StageDiffAggregateTests(SimpleTestCase):
    def test_untouched_rows_are_not_reviewed(self):
        """未填未改 = 没审，不是"审完没意见"。混成采纳会让 Skill 以为这版挺好。"""
        result = sd.build_item_diffs(_stage_result(), [_row(), _row()])
        self.assertEqual(result, [])

    def test_touched_row_is_kept(self):
        result = sd.build_item_diffs(_stage_result(), [_row(人工结论="采纳")])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["item_id"], "TP-01")
        self.assertEqual(result[0]["kinds"], ["adopted"])
        self.assertEqual(result[0]["kind_labels"], ["原样采纳"])

    def test_only_declared_purposes_count_as_additions(self):
        additions = sd.build_additions([
            _attachment(purpose=ATTACHMENT_PURPOSE_CONFIRMED, pk=1),
            _attachment(purpose=ATTACHMENT_PURPOSE_SUPPLEMENT, pk=2),
            _attachment(purpose=ATTACHMENT_PURPOSE_REFERENCE, pk=3),
            _attachment(purpose=ATTACHMENT_PURPOSE_ISSUE, pk=4),
        ])
        self.assertEqual(len(additions), 2)
        self.assertEqual([item["kinds"] for item in additions], [["added"], ["added"]])

    def test_retired_attachment_is_not_an_addition(self):
        additions = sd.build_additions([
            _attachment(purpose=ATTACHMENT_PURPOSE_SUPPLEMENT, retired=True),
        ])
        self.assertEqual(additions, [])

    def test_contract_issues_come_from_quality_only(self):
        quality = {
            "gate": {"failures": [{"code": "schema", "message": "items 缺 id"}]},
            "warnings": [{"code": "dup", "message": "疑似重复项"}],
        }
        issues = sd.build_contract_issues(quality)
        self.assertEqual(
            [(item["severity"], item["code"]) for item in issues],
            [("failure", "schema"), ("warning", "dup")],
        )
        self.assertEqual(sd.build_contract_issues(None), [])

    def test_summary_counts_and_change_flag(self):
        empty = sd.summarize([], [], [])
        self.assertFalse(empty["has_human_change"])
        self.assertEqual(set(empty["counts"]), set(sd.DIFF_KINDS))

        item = sd.build_item_diffs(_stage_result(), [_row(优先级="中")])[0]
        summary = sd.summarize([item], [], [])
        self.assertTrue(summary["has_human_change"])
        self.assertEqual(summary["reviewed_items"], 1)
        self.assertEqual(summary["field_diff_count"], 1)
        self.assertEqual(summary["counts"]["modified"], 1)

    def test_build_stage_diff_shape(self):
        result = sd.build_stage_diff(
            _stage_result(), [_row(人工结论="采纳", 优先级="中")],
            attachments=[_attachment(purpose=ATTACHMENT_PURPOSE_SUPPLEMENT)],
        )
        self.assertEqual(result["version"], "stage-diff/v1")
        self.assertIn("TP-01", result["by_item"])
        self.assertEqual(len(result["additions"]), 1)
        self.assertEqual(result["summary"]["counts"]["added"], 1)


# ------------------------------------------------------------------ 反查链


class ReverseTraceTests(SimpleTestCase):
    def test_chain_follows_declared_order(self):
        result = reverse_trace(_graph(), item_id="TP-01")
        self.assertEqual([node["type"] for node in result["chain"]], list(REVERSE_CHAIN_ORDER))
        self.assertEqual(result["missing"], [])
        self.assertTrue(result["complete"])
        # 从修改到规则的固定叙事：第一段必须是人工修改，最后一段是 Skill 规则。
        self.assertEqual(result["chain"][0]["type"], "HumanEdit")
        self.assertEqual(result["chain"][-1]["type"], "SkillRule")

    def test_missing_types_are_exposed_not_hidden(self):
        graph = _graph()
        graph["nodes"] = [
            node for node in graph["nodes"]
            if node["type"] not in {"HumanEdit", "SkillRule"}
        ]
        result = reverse_trace(graph, item_id="TP-01")
        self.assertIn("HumanEdit", result["missing"])
        self.assertIn("SkillRule", result["missing"])
        self.assertFalse(result["complete"])

    def test_empty_graph_reports_full_break(self):
        result = reverse_trace({}, item_id="TP-01")
        self.assertEqual(result["chain"], [])
        self.assertEqual(result["missing"], list(REVERSE_CHAIN_ORDER))
        self.assertFalse(result["complete"])

    def test_unknown_item_is_not_an_exception(self):
        result = reverse_trace(_graph(), item_id="TP-99")
        self.assertEqual(result["chain"], [])
        self.assertFalse(result["complete"])


# ------------------------------------------------------------------- 归因假设


class HumanEditHypothesisTests(SimpleTestCase):
    def test_graph_signals_split_retrieved_from_verified(self):
        signals = HumanEditAttributionService.graph_signals({
            "evidence_status": {
                "EV-01": {"status": "retrieved"},
                "EV-02": {"status": "verified"},
                "EV-03": {"status": "invalid"},
                "EV-04": {"status": "contradicted"},
            },
        })
        self.assertEqual(signals["total"], 4)
        self.assertEqual(signals["retrieved_unused"], ["EV-01"])
        self.assertEqual(signals["verified"], ["EV-02"])
        self.assertEqual(signals["invalid"], ["EV-03"])
        self.assertEqual(signals["contradicted"], ["EV-04"])

    def test_invalid_evidence_maps_to_retrieval_error(self):
        item = {
            "item_id": "TP-01", "title": "x", "kinds": ["evidence_error"],
            "edit_category": "证据错误", "evidence_ids": ["EV-03"], "field_diffs": [],
        }
        proposal = HumanEditAttributionService.hypothesize(
            item, graph={"evidence_status": {"EV-03": {"status": "invalid"}}},
        )
        self.assertEqual(proposal["category"], "retrieval_error")
        self.assertGreaterEqual(proposal["confidence"], 0.85)
        self.assertIn("无法定位", proposal["reason"])

    def test_contradicted_evidence_maps_to_stale_knowledge(self):
        """依据存在反证 ≠ 检索失败：知识本身已不成立，改检索没用。"""
        item = {
            "item_id": "TP-01", "title": "x", "kinds": ["evidence_error"],
            "edit_category": "证据错误", "evidence_ids": ["EV-04"], "field_diffs": [],
        }
        proposal = HumanEditAttributionService.hypothesize(
            item, graph={"evidence_status": {"EV-04": {"status": "contradicted"}}},
        )
        self.assertEqual(proposal["category"], "knowledge_stale")

    def test_locatable_evidence_becomes_counterevidence(self):
        """引用定位得到，人却判"证据错误" → 记反证并压低置信度。"""
        item = {
            "item_id": "TP-01", "title": "x", "kinds": ["evidence_error"],
            "edit_category": "证据错误", "evidence_ids": ["EV-02"], "field_diffs": [],
        }
        proposal = HumanEditAttributionService.hypothesize(
            item, graph={"evidence_status": {"EV-02": {"status": "verified"}}},
        )
        self.assertEqual(proposal["category"], "retrieval_error")
        self.assertLessEqual(proposal["confidence"], 0.6)
        self.assertEqual(
            [entry["type"] for entry in proposal["counterevidence"]], ["evidence_locatable"],
        )

    def test_requirement_gap_with_available_knowledge_is_prompt_error(self):
        """"召回了但没被引用"是"知识在、没用上"的唯一痕迹。判成"知识缺失"
        会把一条 Prompt 问题送去做知识补录，方向就错了。"""
        item = {
            "item_id": "TP-02", "title": "x", "kinds": ["requirement_gap"],
            "edit_category": "覆盖不足", "requirement_ids": ["REQ-01"], "field_diffs": [],
        }
        proposal = HumanEditAttributionService.hypothesize(
            item, graph={"evidence_status": {"KN-09": {"status": "retrieved"}}},
        )
        self.assertEqual(proposal["category"], "prompt_error")
        self.assertEqual(
            [entry["type"] for entry in proposal["counterevidence"]],
            ["knowledge_available"],
        )

    def test_requirement_gap_without_knowledge_is_missing(self):
        item = {
            "item_id": "TP-02", "title": "x", "kinds": ["requirement_gap"],
            "edit_category": "覆盖不足", "requirement_ids": ["REQ-01"], "field_diffs": [],
        }
        proposal = HumanEditAttributionService.hypothesize(
            item, graph={"evidence_status": {}},
        )
        self.assertEqual(proposal["category"], "knowledge_missing")

    def test_unknown_category_falls_back_to_generation(self):
        item = {
            "item_id": "TP-03", "title": "x", "kinds": ["modified"],
            "edit_category": "", "field_diffs": [{"column": "优先级"}],
        }
        proposal = HumanEditAttributionService.hypothesize(item, graph=None)
        self.assertEqual(proposal["category"], "generation_error")
        self.assertIn("优先级", proposal["hypothesis"])

    def test_edit_category_rules_are_module_level_truth(self):
        self.assertEqual(EDIT_CATEGORY_RULES["证据错误"][0], "retrieval_error")
        self.assertEqual(EDIT_CATEGORY_RULES["优先级不当"][0], "planning_error")

    def test_environment_attribution_is_excluded_from_content_patch(self):
        environment = SimpleNamespace(layer="environment", category="environment_error")
        content = SimpleNamespace(layer="skill_tool", category="generation_error")
        blank = SimpleNamespace(layer="", category="knowledge_missing")

        usable = assert_usable_for_content_patch([environment, content, blank])

        self.assertEqual([item.category for item in usable], ["generation_error", "knowledge_missing"])


# --------------------------------------------------------------- 归因落库


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class HumanEditAttributionTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t11")

    def _run(self, rows, graph=None):
        return HumanEditAttributionService.run_for_output(
            self.output, stage_result=_stage_result(), rows=rows, graph=graph,
        )

    def test_every_attribution_starts_proposed(self):
        results = self._run([_row(人工结论="删除")])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].state, "proposed")
        self.assertEqual(results[0].source, "rule")
        self.assertEqual(results[0].layer, "skill_tool")

    def test_rerun_is_idempotent(self):
        first = self._run([_row(人工结论="删除")])
        second = self._run([_row(人工结论="删除")])
        self.assertEqual([item.pk for item in first], [item.pk for item in second])

    def test_rerun_does_not_reset_human_decision(self):
        """人工确认过的结论不能被一次"重新归因"重置回待确认——
        那会让确认这道闸门变成可撤销的摆设。"""
        attribution = self._run([_row(人工结论="删除")])[0]
        AttributionService.decide(attribution=attribution, actor=self.lead, accepted=True)
        attribution.refresh_from_db()
        self.assertEqual(attribution.state, "confirmed")

        self._run([_row(人工结论="删除")])
        attribution.refresh_from_db()
        self.assertEqual(attribution.state, "confirmed")
        self.assertEqual(attribution.confirmed_by_id, self.lead.pk)

    def test_environment_failure_becomes_its_own_attribution(self):
        SpanRecorder.record(
            trace=self.output.trace, stage=STAGE, step_type="tool", status="failed",
            workflow_id="wf-t11", tool_name="knowledge_search", error_type="timeout",
        )
        results = self._run([_row(人工结论="采纳")])

        categories = sorted(item.category for item in results)
        self.assertEqual(categories, ["environment_error", "generation_error"])
        environment = next(item for item in results if item.category == "environment_error")
        self.assertEqual(environment.layer, "environment")
        self.assertEqual(assert_usable_for_content_patch([environment]), [])

    def test_rewrite_recomputes_layer_and_marks_source(self):
        attribution = self._run([_row(人工结论="删除")])[0]
        self.assertEqual(attribution.layer, "skill_tool")

        AttributionService.rewrite(
            attribution, actor=self.lead, category="knowledge_missing", note="核对过",
        )
        attribution.refresh_from_db()

        self.assertEqual(attribution.category, "knowledge_missing")
        # 层是候选补丁改哪个文件的依据：留旧值会让"知识缺失"仍然去改 SKILL.md。
        self.assertEqual(attribution.layer, "knowledge")
        self.assertEqual(attribution.source, "human")
        self.assertEqual(attribution.state, "proposed")

    def test_decide_records_note(self):
        attribution = self._run([_row(人工结论="删除")])[0]
        AttributionService.decide(
            attribution=attribution, actor=self.lead, accepted=False, note="不是这个问题",
        )
        attribution.refresh_from_db()
        self.assertEqual(attribution.state, "rejected")
        self.assertEqual(attribution.evidence[-1]["type"], "human_review")
        self.assertEqual(attribution.evidence[-1]["note"], "不是这个问题")


# ------------------------------------------------------------------ 接口


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class T11ApiTests(SkillHubBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.base = "/api/knowledge-evolution/operations/"
        self.output = self.make_output(task_type=STAGE, workflow_id="wf-t11-api")
        self.output.metadata = {**(self.output.metadata or {}), "stage_result_payload": _payload()}
        self.output.save(update_fields=["metadata"])

    def _params(self) -> dict:
        return {"project": self.project.pk, "workflow_id": "wf-t11-api", "stage": STAGE}

    def _post(self, path: str, payload: dict):
        return self.client.post(f"{self.base}{path}", {**self._params(), **payload}, format="json")

    def _human_event(self, rows) -> FeedbackEvent:
        return FeedbackEvent.objects.create(
            project=self.project, output=self.output, trace=self.output.trace,
            signal="accepted", reason_code=REVIEW_REASON_CODE,
            idempotency_key="t11-review-1", actor=self.lead,
            detail={"state": "submitted", "human_rows": rows},
        )

    def test_diff_without_structured_protocol_is_400(self):
        self.output.metadata = {"protocol": {"workflow_id": "wf-t11-api"}}
        self.output.save(update_fields=["metadata"])
        response = self.client.get(f"{self.base}workflow-stage-diff/", self._params())
        self.assertEqual(response.status_code, 400)

    def test_diff_is_explicit_when_no_review_was_uploaded(self):
        """差异为空时必须能区分"审完了没改"和"平台压根没拿到人工结论"。"""
        response = self.client.get(f"{self.base}workflow-stage-diff/", self._params())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["review_rows_available"])
        self.assertEqual(response.data["diff"]["items"], [])

    def test_diff_reports_field_level_change_and_reverse_trace(self):
        self._human_event([_row(人工结论="修改后采纳", 修改类型="优先级不当", 优先级="中")])

        response = self.client.get(f"{self.base}workflow-stage-diff/", self._params())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["review_rows_available"])
        item = response.data["diff"]["items"][0]
        self.assertEqual(item["item_id"], "TP-01")
        self.assertIn("modified", item["kinds"])
        change = next(d for d in item["field_diffs"] if d["field"] == "priority")
        self.assertEqual((change["before"], change["after"]), ("高", "中"))
        # 没有登记派生物时反查链显式断在起点，而不是画一条看起来完整的链。
        self.assertFalse(response.data["derived"]["available"])
        self.assertFalse(response.data["reverse_traces"]["TP-01"]["complete"])

    def test_run_attribution_requires_review_rows(self):
        response = self._post("workflow-stage-attribution-run/", {})
        self.assertEqual(response.status_code, 400)

    def test_run_attribution_then_confirm(self):
        self._human_event([_row(人工结论="删除")])

        run = self._post("workflow-stage-attribution-run/", {})
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.data["created"], 1)
        attribution = run.data["attributions"][0]
        self.assertEqual(attribution["state"], "proposed")
        self.assertTrue(attribution["usable_for_content_patch"])

        confirmed = self._post("workflow-stage-attribution-decide/", {
            "attribution_id": attribution["id"], "action": "confirm", "note": "确实写错了",
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.data["state"], "confirmed")
        self.assertEqual(confirmed.data["state_label"], "已确认")

    def test_rejected_attribution_stays_rejected_after_rerun(self):
        self._human_event([_row(人工结论="删除")])
        attribution_id = self._post(
            "workflow-stage-attribution-run/", {},
        ).data["attributions"][0]["id"]

        self._post("workflow-stage-attribution-decide/", {
            "attribution_id": attribution_id, "action": "reject", "note": "不是模型的问题",
        })
        self._post("workflow-stage-attribution-run/", {})

        listed = self.client.get(f"{self.base}workflow-stage-diff/", self._params())
        entry = next(
            item for item in listed.data["attributions"] if item["id"] == attribution_id
        )
        self.assertEqual(entry["state"], "rejected")
        self.assertEqual(listed.data["attribution_summary"]["rejected"], 1)

    def test_bad_action_is_400(self):
        self._human_event([_row(人工结论="删除")])
        attribution_id = self._post(
            "workflow-stage-attribution-run/", {},
        ).data["attributions"][0]["id"]

        response = self._post("workflow-stage-attribution-decide/", {
            "attribution_id": attribution_id, "action": "maybe",
        })
        self.assertEqual(response.status_code, 400)

    def test_rewrite_changes_layer_and_category(self):
        self._human_event([_row(人工结论="删除")])
        attribution_id = self._post(
            "workflow-stage-attribution-run/", {},
        ).data["attributions"][0]["id"]

        response = self._post("workflow-stage-attribution-rewrite/", {
            "attribution_id": attribution_id, "category": "knowledge_missing",
            "hypothesis": "其实是知识库里没有这条依据", "note": "人工核对过",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["category"], "knowledge_missing")
        self.assertEqual(response.data["layer"], "knowledge")
        self.assertEqual(response.data["source"], "human")

    def test_rewrite_unknown_category_is_400(self):
        self._human_event([_row(人工结论="删除")])
        attribution_id = self._post(
            "workflow-stage-attribution-run/", {},
        ).data["attributions"][0]["id"]

        response = self._post("workflow-stage-attribution-rewrite/", {
            "attribution_id": attribution_id, "category": "not_a_category",
        })
        self.assertEqual(response.status_code, 400)

    def test_environment_attribution_is_marked_unusable(self):
        SpanRecorder.record(
            trace=self.output.trace, stage=STAGE, step_type="tool", status="failed",
            workflow_id="wf-t11-api", tool_name="knowledge_search", error_type="timeout",
        )
        self._human_event([_row(人工结论="采纳")])

        response = self._post("workflow-stage-attribution-run/", {})

        self.assertEqual(response.status_code, 200)
        environment = next(
            item for item in response.data["attributions"]
            if item["category"] == "environment_error"
        )
        self.assertFalse(environment["usable_for_content_patch"])
        self.assertNotEqual(environment["excluded_reason"], "")

    def test_executor_cannot_decide(self):
        self._human_event([_row(人工结论="删除")])
        attribution_id = self._post(
            "workflow-stage-attribution-run/", {},
        ).data["attributions"][0]["id"]

        self.client.force_authenticate(self.executor)
        response = self._post("workflow-stage-attribution-decide/", {
            "attribution_id": attribution_id, "action": "confirm",
        })
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_read_diff(self):
        from django.contrib.auth.models import User

        outsider = User.objects.create_user(username="t11-outsider", password="p")
        self.client.force_authenticate(outsider)
        response = self.client.get(f"{self.base}workflow-stage-diff/", self._params())
        self.assertEqual(response.status_code, 403)

    def test_member_of_another_project_cannot_use_this_stage(self):
        self.client.force_authenticate(self.executor)
        response = self.client.get(f"{self.base}workflow-stage-diff/", {
            "project": self.other_project.pk, "workflow_id": "wf-t11-api", "stage": STAGE,
        })
        self.assertEqual(response.status_code, 403)
