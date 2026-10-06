"""T05：``stage-result/v1`` 与 Skill 兼容等级。

验收要盯住四件事，它们各自都对应一种"页面会骗人"的失败：

- 旧 Skill 没有 ``stage_result.json`` 时必须能按 L0/L1 正常跑完——否则这次升级
  会把存量 Skill 全部卡死；
- 合法的 L2/L3 必须能被同一套解析器认出来，而不是每个阶段各写一份判断；
- 缺稳定 ID / 缺主产物 / 非法引用必须给出**具体字段路径**，否则错误报告只是
  把定位工作推回给 Skill 作者；
- 结构化协议失败与业务生成失败必须是两个标记，而且在协议失败时**业务文件不能被删**。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from knowledge_evolution import stage_outputs as so
from knowledge_evolution.models import GenerationOutput
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests


def _l3_payload() -> dict:
    return {
        "schema_version": "stage-result/v1",
        "stage": "test_plan_generation",
        "status": "completed",
        "primary_artifacts": [{"name": "test_plan.xlsx", "kind": "xlsx"}],
        "requirements": [{"id": "REQ-12", "title": "非白名单账户校验"}],
        "items": [
            {
                "id": "TP-018",
                "title": "非白名单账户反向校验",
                "requirement_ids": ["REQ-12"],
                "evidence_ids": ["EV-001"],
                "scenario_type": "negative",
                "priority": "P0",
                "expected": "非白名单账户被拒绝并记录审计日志",
            }
        ],
        "evidence": [
            {
                "id": "EV-001",
                "document_id": "doc-21",
                "document_version": "v3",
                "chunk_id": "chunk-108",
                "quote": "非白名单证券账户不得进入后续投票处理流程。",
                "content_hash": "sha256:abcd",
            }
        ],
        "decision_summary": [{"decision": "按反向场景覆盖", "reason": "业务规则明确禁止"}],
        "uncertainties": ["白名单更新时效待确认"],
    }


class SchemaFileTests(SimpleTestCase):
    def test_schema_file_is_draft07_and_identified(self):
        schema = so.load_schema()
        self.assertEqual(schema["$id"], "stage-result/v1")
        self.assertIn("draft-07", schema["$schema"])
        for key in ("schema_version", "stage", "status", "primary_artifacts", "items", "evidence"):
            self.assertIn(key, schema["required"])

    def test_level_truth_is_module_level_and_ordered(self):
        # 项目铁律：语义集合必须是模块级真值；写成类属性会在 import 期炸。
        self.assertEqual(so.COMPATIBILITY_LEVELS, ("L0", "L1", "L2", "L3"))
        self.assertEqual(so.LEVEL_RANK["L3"], 3)
        for level in so.COMPATIBILITY_LEVELS:
            self.assertIn(level, so.COMPATIBILITY_LABELS)
            self.assertIn(level, so.COMPATIBILITY_CAPABILITIES)

    def test_normalize_level_falls_back_to_l0(self):
        self.assertEqual(so.normalize_level("l3"), "L3")
        self.assertEqual(so.normalize_level("L9"), "L0")
        self.assertEqual(so.normalize_level(None), "L0")
        self.assertFalse(so.is_known_level("L9"))
        self.assertTrue(so.level_at_least("L3", "L2"))
        self.assertFalse(so.level_at_least("L1", "L2"))


class ValidPayloadTests(SimpleTestCase):
    def test_full_payload_is_l3(self):
        result = so.validate_stage_result(_l3_payload(), expected_stage="test_plan_generation")
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L3")
        self.assertEqual(result.effective_level, "L3")
        self.assertEqual(result.result_marker, so.RESULT_OK)
        self.assertEqual(result.issues, [])

    def test_items_without_evidence_are_l2(self):
        """evidence 是必填字段，但可以是空数组——L2 正是"有 items、没证据链"。"""
        payload = _l3_payload()
        payload["evidence"] = []
        payload.pop("decision_summary")
        payload["items"][0].pop("evidence_ids")
        payload["items"][0].pop("requirement_ids")
        payload.pop("requirements")

        result = so.validate_stage_result(payload)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L2")

    def test_primary_artifact_without_items_is_l1(self):
        payload = {
            "schema_version": "stage-result/v1",
            "stage": "test_plan_generation",
            "status": "completed",
            "primary_artifacts": [{"name": "test_plan.xlsx"}],
            "items": [],
            "evidence": [],
        }
        result = so.validate_stage_result(payload)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L1")

    def test_items_without_stable_id_stop_at_l1(self):
        """没有稳定 ID 就不能当 L2——否则重跑时整份方案会被算成全新内容。"""
        payload = {
            "schema_version": "stage-result/v1",
            "stage": "test_plan_generation",
            "status": "completed",
            "primary_artifacts": [{"name": "test_plan.xlsx"}],
            "items": [{"id": "  ", "title": "无 ID 项"}],
            "evidence": [],
        }
        self.assertEqual(so.classify_level(payload), "L1")

    def test_json_string_input_is_accepted(self):
        result = so.validate_stage_result(json.dumps(_l3_payload()))
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L3")

    def test_bytes_input_is_accepted(self):
        result = so.validate_stage_result(json.dumps(_l3_payload()).encode("utf-8"))
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L3")


class FieldLevelErrorTests(SimpleTestCase):
    """错误必须落在具体字段上。"""

    def _paths(self, result):
        return {issue.path for issue in result.issues}

    def test_missing_primary_artifacts_points_at_the_field(self):
        payload = _l3_payload()
        payload["primary_artifacts"] = []
        result = so.validate_stage_result(payload)
        self.assertFalse(result.ok)
        self.assertIn("/primary_artifacts", self._paths(result))
        self.assertEqual(result.level, "L0")

    def test_missing_stable_id_is_reported(self):
        payload = _l3_payload()
        payload["items"][0]["id"] = ""
        result = so.validate_stage_result(payload)
        self.assertFalse(result.ok)
        self.assertIn("/items/0/id", self._paths(result))
        self.assertIn("missing_stable_id", {issue.code for issue in result.issues})

    def test_duplicate_item_id_is_reported(self):
        payload = _l3_payload()
        payload["items"].append(dict(payload["items"][0], title="重复项"))
        result = so.validate_stage_result(payload)
        self.assertFalse(result.ok)
        self.assertIn("duplicate_item_id", {issue.code for issue in result.issues})

    def test_unknown_evidence_reference_is_reported(self):
        payload = _l3_payload()
        payload["items"][0]["evidence_ids"] = ["EV-404"]
        result = so.validate_stage_result(payload)
        self.assertFalse(result.ok)
        self.assertIn("/items/0/evidence_ids/0", self._paths(result))
        self.assertIn("unknown_evidence_reference", {issue.code for issue in result.issues})

    def test_unknown_requirement_reference_is_reported(self):
        payload = _l3_payload()
        payload["items"][0]["requirement_ids"] = ["REQ-404"]
        result = so.validate_stage_result(payload)
        self.assertFalse(result.ok)
        self.assertIn("/items/0/requirement_ids/0", self._paths(result))
        self.assertIn("unknown_requirement_reference", {issue.code for issue in result.issues})

    def test_requirement_reference_not_checked_without_requirement_list(self):
        """没声明需求清单时无法判定引用对错，不该凭空报错。"""
        payload = _l3_payload()
        payload.pop("requirements")
        result = so.validate_stage_result(payload)
        self.assertTrue(result.ok, result.as_dict())

    def test_stage_mismatch_is_reported(self):
        result = so.validate_stage_result(_l3_payload(), expected_stage="testcase_generation")
        self.assertFalse(result.ok)
        self.assertIn("stage_mismatch", {issue.code for issue in result.issues})

    def test_invalid_json_is_reported_as_structured_failure(self):
        result = so.validate_stage_result("{不是 JSON")
        self.assertFalse(result.ok)
        self.assertEqual(result.result_marker, so.RESULT_STRUCTURED_FAILURE)
        self.assertEqual({issue.code for issue in result.issues}, {"invalid_json"})

    def test_unknown_schema_version_degrades_to_l0_without_failing(self):
        payload = _l3_payload()
        payload["schema_version"] = "stage-result/v2"
        result = so.validate_stage_result(payload)
        self.assertTrue(result.ok)
        self.assertEqual(result.effective_level, "L0")
        self.assertIn("v2", result.detail)


class DeclaredLevelTests(SimpleTestCase):
    def test_read_declared_level_supports_both_shapes(self):
        self.assertEqual(so.read_declared_level({"stage_result_level": "l3"}), "L3")
        self.assertEqual(so.read_declared_level({"stage_result": {"level": "L2"}}), "L2")
        self.assertEqual(so.read_declared_level({}), "")
        self.assertEqual(so.read_declared_level(None), "")

    def test_declared_above_measured_takes_the_lower(self):
        payload = _l3_payload()
        payload["items"][0].pop("evidence_ids")
        payload["items"][0].pop("requirement_ids")
        payload.pop("requirements")
        payload["evidence"] = []

        result = so.validate_stage_result(payload, declared_level="L3")
        self.assertTrue(result.ok)
        self.assertEqual(result.level, "L2")
        self.assertEqual(result.effective_level, "L2")
        self.assertTrue(result.level_gap)
        self.assertIn("未达成", result.detail)

    def test_measured_above_declared_takes_the_lower(self):
        result = so.validate_stage_result(_l3_payload(), declared_level="L1")
        self.assertTrue(result.ok)
        self.assertEqual(result.level, "L3")
        self.assertEqual(result.effective_level, "L1")
        self.assertFalse(result.level_gap)
        self.assertIn("仅声明 L1", result.detail)


class LegacySkillCompatibilityTests(SimpleTestCase):
    """存量 Skill 没有 stage_result.json 时必须仍能跑。"""

    def test_absence_of_file_is_not_a_hard_error_for_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "stage_result.json"
            result = so.read_stage_result_file(missing)
        self.assertFalse(result.ok)
        self.assertEqual({issue.code for issue in result.issues}, {"missing_file"})
        # 关键：这只是"没有信封"，不是"跑失败了"。等级停在 L0，业务产物照常。
        self.assertEqual(result.effective_level, "L0")
        self.assertEqual(result.result_marker, so.RESULT_STRUCTURED_FAILURE)

    def test_protocol_failure_never_deletes_business_artifacts(self):
        """结构化协议失败不许动业务文件——这是本次升级最容易踩坏存量能力的地方。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            business = root / "test_plan.xlsx"
            business.write_bytes(b"business-payload")
            broken = root / "stage_result.json"
            broken.write_text('{"schema_version": "stage-result/v1", "stage": "x"}', encoding="utf-8")

            before = business.read_bytes()
            result = so.read_stage_result_file(broken)

            self.assertFalse(result.ok)
            self.assertTrue(business.exists(), "协议失败不得删除业务主产物")
            self.assertEqual(business.read_bytes(), before)
            self.assertTrue(broken.exists(), "协议失败不得删除 stage_result.json 本身")

    def test_healthy_file_is_validated_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stage_result.json"
            path.write_text(json.dumps(_l3_payload(), ensure_ascii=False), encoding="utf-8")
            result = so.read_stage_result_file(path)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L3")


class StageResultNormalizationTests(SimpleTestCase):
    def test_from_payload_normalizes_and_exposes_ids(self):
        validation = so.validate_stage_result(_l3_payload())
        normalized = so.StageResult.from_payload(validation.payload, validation.level)

        self.assertEqual(normalized.stage, "test_plan_generation")
        self.assertEqual(normalized.status, "completed")
        self.assertEqual(normalized.level, "L3")
        self.assertEqual(normalized.item_ids(), ["TP-018"])
        self.assertEqual(normalized.evidence_ids(), ["EV-001"])
        self.assertEqual(normalized.uncertainties, ["白名单更新时效待确认"])

    def test_result_marker_distinguishes_failure_kinds(self):
        # 结构化协议失败与业务生成失败是两个标记，页面上必须能分开显示。
        self.assertNotEqual(so.RESULT_STRUCTURED_FAILURE, so.RESULT_BUSINESS_FAILURE)
        self.assertNotEqual(so.RESULT_OK, so.RESULT_STRUCTURED_FAILURE)

    def test_as_dict_is_json_serializable(self):
        validation = so.validate_stage_result({"stage": "x"})
        text = json.dumps(validation.as_dict(), ensure_ascii=False)
        self.assertIn("result_marker", text)


class SkillHubLevelDisplayTests(SkillHubBaseTests):
    """Skill Hub 必须把「声明等级」和「最近一次实测结论」分开显示。

    合成一个字段会让"声称 L3、实际只交了 L2"这种事在页面上看不出来——
    而那正是 Skill 作者最需要知道的一条反馈。
    """

    def _serialize(self, skills):
        from skills.serializers import SkillListSerializer
        from skills.views import SkillViewSet

        return SkillListSerializer(
            skills, many=True,
            context={
                'versions': SkillViewSet._display_versions(skills),
                'stage_result_runs': SkillViewSet._last_stage_result_runs(skills),
            },
        ).data

    def test_undeclared_skill_shows_empty_level_and_no_run(self):
        skill, _ = self.make_skill_version()
        row = self._serialize([skill])[0]
        self.assertEqual(row['stage_result_level'], '')
        self.assertEqual(row['stage_result_level_label'], '')
        self.assertEqual(row['stage_result_capability'], '')
        self.assertIsNone(row['stage_result_last_run'])

    def test_declared_level_is_displayed_with_label_and_capability(self):
        skill, version = self.make_skill_version()
        version.manifest = {**(version.manifest or {}), 'stage_result_level': 'L3'}
        version.save(update_fields=['manifest'])

        row = self._serialize([skill])[0]
        self.assertEqual(row['stage_result_level'], 'L3')
        self.assertEqual(row['stage_result_level_label'], 'L3 可进化产物')
        self.assertIn('归因', row['stage_result_capability'])

    def test_last_run_is_read_from_latest_output_metadata(self):
        skill, version = self.make_skill_version()
        trace = self.make_trace()
        validation = so.validate_stage_result({'stage': 'case_review'}, declared_level='L3')
        GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type='case_review',
            task_id='t', content='x', output_hash='h'.ljust(64, '0'),
            skill_version=version,
            metadata={
                'stage_result_submitted': True,
                'compatibility_level': 'L0',
                'stage_result_validation': validation.as_dict(),
            },
        )

        run = self._serialize([skill])[0]['stage_result_last_run']
        self.assertIsNotNone(run)
        # 页面靠 result_marker 区分"结构化协议失败"与"业务生成失败"。
        self.assertEqual(run['result_marker'], so.RESULT_STRUCTURED_FAILURE)
        self.assertEqual(run['effective_level'], 'L0')
        self.assertTrue(run['recorded_at'])

    def test_output_without_structured_conclusion_does_not_occupy_the_slot(self):
        """旁路产出也会落在同一个 Skill 版本下，但它没跑过受控阶段。"""
        skill, version = self.make_skill_version()
        trace = self.make_trace()
        GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type='case_review',
            task_id='t', content='x', output_hash='h'.ljust(64, '0'),
            skill_version=version, metadata={'protocol': {}},
        )
        self.assertIsNone(self._serialize([skill])[0]['stage_result_last_run'])
