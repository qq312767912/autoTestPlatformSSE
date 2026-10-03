"""R4：AI 提候选优化点 → 人工逐条确认。**AI 的结论永远是 proposed。**

本文件钉住四件事：

1. **无 LLM 是可识别的降级，不是故障**：返回 200 + ``degraded=true``，
   而不是 500 让页面弹"系统错误"。
2. **AI 的结论必须落 ``proposed``**，且 ``confidence`` 有上限 ——
   落 ``confirmed`` 就等于把"AI 觉得自己错了"当成改进依据。
3. **人工确认不抬高置信度**：``accept`` 之后仍是原来那个数。
   写成 1.0 会让人事后分不清"模型猜对了"与"人确认过"。
4. **派生只吃已确认的**：被驳回的、还没确认的都不许进派生输入。

判据来源：``specs/evolution-assisted-loop/{design,tasks}.md`` T06 / T07。
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.test import override_settings

from knowledge_evolution.models import GenerationOutput  # noqa: F401  (模型注册)
from knowledge_evolution.report_optimization import (
    FALSE_ALARM_CATEGORIES,
    ReportOptimizationAdvisor,
    signal_for_category,
)
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT
from knowledge_evolution.tests_t23_case_review_evolution import (
    CaseReviewApiBase,
    make_report,
)
from knowledge_evolution.trace_models import FailureAttribution

BASE = "/api/knowledge-evolution/case-review-evolution/"
PROPOSE_URL = f"{BASE}attributions/"
CONFIRM_URL = f"{BASE}attributions/confirm/"
EVOLVE_URL = f"{BASE}evolve/"

#: 假的模型响应：两条候选，一条误报类、一条缺陷类。
FAKE_LLM_PAYLOAD = {
    "candidates": [
        {
            "category": "prompt_error",
            "issue_type": "措辞冗余",
            "hypothesis": "把「简洁」判定条件收紧：等价表述不算冗余",
            "confidence": 0.95,  # 故意给 0.95，验证服务端会压到 0.8 以下
            "evidence": ["多条被标为误报，理由都是原文已含等价表述"],
            "counterevidence": ["也有 1 条人工认可了该判定"],
        },
        {
            "category": "generation_error",
            "issue_type": "预期结果不可验收",
            "hypothesis": "预期结果必须落到可观测断言，禁止「功能正常」这类表述",
            "confidence": 0.6,
            "evidence": ["人工改写了预期结果列"],
            "counterevidence": [],
        },
    ]
}


def fake_factory(response_text: str):
    """造一个 ``llm_factory``：返回带 ``.invoke()`` 的假模型。"""

    class _FakeLLM:
        def invoke(self, prompt):  # noqa: ARG002 - 只要返回内容即可
            return response_text

    class _FakeFactory:
        def __call__(self, config):  # noqa: ARG002
            return _FakeLLM()

    return _FakeFactory()


def friendly_report():
    """一份含两类缺陷的报告，供候选生成使用。"""
    return make_report(
        [
            {"问题类型": "措辞冗余", "问题确认": "否", "不采纳原因": "原文已含等价表述", "行号": 3},
            {"问题类型": "预期结果不可验收", "问题确认": "是",
             "问题描述": "预期结果不可验收", "修改点": "改为：返回码 0000", "行号": 5},
        ],
        acceptance_rate=0.62,
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ProposalWithoutLlmTests(CaseReviewApiBase):
    """未配置 LLM：降级要可见，不能变成 500。"""

    def test_no_active_llm_returns_recognizable_degradation(self):
        response = self.client.post(
            PROPOSE_URL,
            {"project": self.project.id, "review_id": str(self.review.pk),
             "file": self._upload(friendly_report())},
            format="multipart",
        )

        # 未配置模型是一条合法路径，不是服务故障。
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["degraded"])
        self.assertEqual(response.data["reason_code"], "llm_unavailable")
        self.assertEqual(response.data["candidates"], [])
        # 降级路径不该留下半成品归因。
        self.assertFalse(FailureAttribution.objects.filter(project=self.project).exists())

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            PROPOSE_URL,
            {"project": self.project.id, "review_id": str(self.review.pk)},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ProposalWithLlmTests(CaseReviewApiBase):
    """有 LLM：候选落 proposed，置信度有上限。"""

    def setUp(self):
        super().setUp()
        from langgraph_integration.models import LLMConfig

        LLMConfig.objects.create(
            config_name="测试配置", provider="openai_compatible", name="gpt-test",
            api_url="http://127.0.0.1:1/v1", api_key="x", is_active=True,
        )

    def _propose(self, *, payload=None):
        payload = payload if payload is not None else FAKE_LLM_PAYLOAD
        with patch.object(
            ReportOptimizationAdvisor, "_default_factory",
            staticmethod(fake_factory(json.dumps(payload, ensure_ascii=False))),
        ):
            return self.client.post(
                PROPOSE_URL,
                {"project": self.project.id, "review_id": str(self.review.pk),
                 "file": self._upload(friendly_report())},
                format="multipart",
            )

    def test_candidates_land_as_proposed_never_confirmed(self):
        """AI 的结论一律 ``proposed``、``source="llm"``。"""
        response = self._propose()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["degraded"])
        self.assertEqual(len(response.data["candidates"]), 2)

        rows = FailureAttribution.objects.filter(project=self.project)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual(row.state, "proposed")
            self.assertEqual(row.source, "llm")
            self.assertIsNone(row.confirmed_by_id)
            self.assertIsNone(row.confirmed_at)

    def test_confidence_is_capped_below_certainty(self):
        """模型给 0.95 也要压到 0.8 以下 —— 确定性只有规则与人工配得上。"""
        response = self._propose()

        confidences = [item["confidence"] for item in response.data["candidates"]]
        self.assertEqual(max(confidences), 0.8)
        self.assertLess(confidences[0], 1.0)

    def test_signal_follows_category_semantics(self):
        """误报类走 ``false_positive``、缺陷类走 ``defect_confirmed``。

        这两个信号在评测器里是正负两端，写反等于要求下次执行的模型做相反的事。
        """
        response = self._propose()
        by_category = {item["category"]: item for item in response.data["candidates"]}

        self.assertEqual(by_category["prompt_error"]["signal"], "false_positive")
        self.assertEqual(by_category["generation_error"]["signal"], "defect_confirmed")
        for category in FALSE_ALARM_CATEGORIES:
            self.assertEqual(signal_for_category(category), "false_positive")
        self.assertNotIn("prompt_error", {c for c in FALSE_ALARM_CATEGORIES
                                          if signal_for_category(c) != "false_positive"})

    def test_regenerating_replaces_instead_of_piling_up(self):
        """同一产出重复生成只保留一轮候选，不堆历史。

        候选是"这一轮报告给出的结论"，不是模型的调用记录。
        """
        self._propose()
        self._propose()

        self.assertEqual(FailureAttribution.objects.filter(project=self.project).count(), 2)

    def test_package_body_is_reported_so_citations_are_checkable(self):
        """返回里要带上"读的是哪一版包、读了哪几个文件"，事后可逐字复核。"""
        response = self._propose()

        package = response.data["package"]
        self.assertEqual(package["version"], self.version.version)
        self.assertEqual(package["package_sha256"], self.version.package_sha256)
        self.assertTrue(any(item["path"] == "SKILL.md" for item in package["files"]))

    def test_llm_returning_nothing_usable_is_a_400(self):
        """模型回了一堆不可用的东西 → 400 并说清原因，不落垃圾归因。"""
        response = self._propose(payload={"candidates": [
            {"category": "不存在的类别", "hypothesis": "x"},
            {"category": "prompt_error", "hypothesis": ""},
        ]})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(FailureAttribution.objects.filter(project=self.project).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ConfirmationTests(CaseReviewApiBase):
    """第 ③ 步后半：逐条采纳 / 改写 / 驳回，然后派生。"""

    def setUp(self):
        super().setUp()
        from langgraph_integration.models import LLMConfig

        LLMConfig.objects.create(
            config_name="测试配置", provider="openai_compatible", name="gpt-test",
            api_url="http://127.0.0.1:1/v1", api_key="x", is_active=True,
        )
        with patch.object(
            ReportOptimizationAdvisor, "_default_factory",
            staticmethod(fake_factory(json.dumps(FAKE_LLM_PAYLOAD, ensure_ascii=False))),
        ):
            response = self.client.post(
                PROPOSE_URL,
                {"project": self.project.id, "review_id": str(self.review.pk),
                 "file": self._upload(friendly_report())},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200)
        self.candidates = response.data["candidates"]
        self.false_alarm = self.candidates[0]      # prompt_error
        self.defect = self.candidates[1]           # generation_error

    def _confirm(self, decisions):
        return self.client.post(
            CONFIRM_URL,
            {"project": self.project.id, "review_id": str(self.review.pk),
             "decisions": json.dumps(decisions, ensure_ascii=False)},
            format="multipart",
        )

    def _evolve(self, ids):
        return self.client.post(
            EVOLVE_URL,
            {"project": self.project.id, "review_id": str(self.review.pk),
             "attribution_ids": json.dumps(ids),
             "file": self._upload(friendly_report())},
            format="multipart",
        )

    def test_accept_confirms_without_raising_confidence(self):
        response = self._confirm([{"attribution_id": self.false_alarm["attribution_id"],
                                   "action": "accept"}])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed_count"], 1)

        row = FailureAttribution.objects.get(id=self.false_alarm["attribution_id"])
        self.assertEqual(row.state, "confirmed")
        self.assertEqual(row.confirmed_by_id, self.lead.id)
        self.assertIsNotNone(row.confirmed_at)
        # 关键：确认不等于"提高到 1.0"。
        self.assertEqual(row.confidence, self.false_alarm["confidence"])
        self.assertLess(row.confidence, 1.0)

    def test_edit_rewrites_the_hypothesis_and_keeps_it_ai_sourced(self):
        response = self._confirm([{
            "attribution_id": self.defect["attribution_id"],
            "action": "edit",
            "hypothesis": "预期结果必须写成可观测断言：返回码 + 报文关键字段",
        }])

        self.assertEqual(response.status_code, 200)
        row = FailureAttribution.objects.get(id=self.defect["attribution_id"])
        self.assertEqual(row.state, "confirmed")
        self.assertIn("可观测断言", row.hypothesis)
        # 改写的是正文，来源仍是 AI 提的 —— 事后要能分清"谁提的、谁改的"。
        self.assertEqual(row.source, "llm")

    def test_reject_removes_it_from_derivation(self):
        """被驳回的候选不能进派生输入。"""
        self._confirm([{"attribution_id": self.false_alarm["attribution_id"],
                        "action": "reject"}])

        row = FailureAttribution.objects.get(id=self.false_alarm["attribution_id"])
        self.assertEqual(row.state, "rejected")

        response = self._evolve([self.false_alarm["attribution_id"]])
        self.assertEqual(response.status_code, 400)
        self.assertIn("已确认", response.data["detail"])

    def test_evolve_refuses_unconfirmed_candidates(self):
        """还没确认就派生 → 400，并说清"先去确认"。"""
        response = self._evolve([self.defect["attribution_id"]])

        self.assertEqual(response.status_code, 400)
        self.assertIn("已确认", response.data["detail"])

    def test_confirmed_candidates_drive_the_derivation(self):
        """确认 1 条 → 派生成功，且派生只拿这一条当依据。"""
        self._confirm([
            {"attribution_id": self.false_alarm["attribution_id"], "action": "accept"},
            {"attribution_id": self.defect["attribution_id"], "action": "reject"},
        ])

        response = self._evolve([self.false_alarm["attribution_id"]])

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["attribution_source"], "llm+human")
        self.assertEqual(
            response.data["attribution_ids"], [self.false_alarm["attribution_id"]],
        )
        self.assertEqual(response.data["candidate"]["state"], "draft")
        # 派生不许动活跃版本。
        self.assertTrue(response.data["active_untouched"])

    def test_missing_attribution_id_is_rejected(self):
        response = self._confirm([{"action": "accept"}])

        self.assertEqual(response.status_code, 400)
        self.assertIn("attribution_id", json.dumps(response.data, ensure_ascii=False))

    def test_unknown_action_is_reported_per_item_not_whole_batch(self):
        """一条写错不该把整批人已做完的确认丢掉。"""
        response = self._confirm([
            {"attribution_id": self.false_alarm["attribution_id"], "action": "accept"},
            {"attribution_id": self.defect["attribution_id"], "action": "whatever"},
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed_count"], 1)
        errors = [item for item in response.data["results"] if item.get("error")]
        self.assertEqual(len(errors), 1)
        # 好的那条照样落库了。
        self.assertEqual(
            FailureAttribution.objects.get(id=self.false_alarm["attribution_id"]).state,
            "confirmed",
        )

    def test_foreign_project_candidate_is_not_reachable(self):
        """确认动作也只能落在本项目的候选上。"""
        from rest_framework.test import APIClient

        outsider = APIClient()
        outsider.force_authenticate(user=self.lead)
        response = outsider.post(
            CONFIRM_URL,
            {"project": self.other_project.id, "review_id": str(self.review.pk),
             "decisions": json.dumps([{
                 "attribution_id": self.false_alarm["attribution_id"], "action": "accept",
             }])},
            format="multipart",
        )

        # review 不属于 other_project → 404；归因绝不能因此被改。
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            FailureAttribution.objects.get(id=self.false_alarm["attribution_id"]).state,
            "proposed",
        )
