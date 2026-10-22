from django.core.exceptions import ValidationError
from django.test import TestCase

from knowledge_evolution.evolution_evidence import CriticEvidencePackage
from knowledge_evolution.evolution_taxonomy import (
    PRIMARY_CAUSES,
    allowed_repair_targets,
    taxonomy_view,
    validate_diagnosis,
)
from knowledge_evolution.tests_t23_case_review_evolution import CaseReviewReportParser, make_report


class AttributionTaxonomyV2Tests(TestCase):
    def test_every_fine_cause_has_at_least_one_repair_target(self):
        for primary_key, primary in PRIMARY_CAUSES.items():
            for fine_key in primary["fine_causes"]:
                self.assertTrue(allowed_repair_targets(primary_key, fine_key))

    def test_invalid_cross_category_route_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_diagnosis("skill_schema", "invalid_tool_argument", "tool_schema")

    def test_taxonomy_view_is_cascade_ready(self):
        view = taxonomy_view()
        self.assertEqual(view["schema_version"], "skill_attribution/v2")
        self.assertTrue(view["primary_causes"])
        self.assertTrue(view["primary_causes"][0]["fine_causes"][0]["repair_targets"])


class CriticEvidencePackageTests(TestCase):
    def test_report_evidence_is_locatable_and_resolvable(self):
        scan = CaseReviewReportParser.parse(make_report([
            {"问题类型": "预期结果模糊", "问题确认": "否", "Sheet": "用例", "行号": 7,
             "不采纳原因": "原文已经可验收"},
        ]))

        class Output:
            pk = "output-1"
            trace = None
            metadata = {}
            content = ""

        package = CriticEvidencePackage.build(output=Output(), scan=scan)
        entries = package.as_prompt_view()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["type"], "report_row")
        self.assertTrue(entries[0]["locatable"])
        resolved, missing = package.resolve([entries[0]["evidence_id"]])
        self.assertEqual(resolved[0]["source_id"], "report:用例:7")
        self.assertEqual(missing, [])

    def test_unknown_evidence_reference_is_rejected(self):
        package = CriticEvidencePackage([])
        resolved, missing = package.resolve(["span:not-found"])
        self.assertEqual(resolved, [])
        self.assertEqual(missing, ["span:not-found"])
