"""T05：人工审核、仲裁与冻结门禁。

覆盖 tasks.md T05 的三条验收：
1. 没有人工审核的资产无法进入冻结版本；
2. 存在冲突或关键场景缺口时无法冻结；
3. 非测试负责人不能复核、仲裁或冻结。

另加两条守门断言：审核动作必须落"标签/分区/分类/理由/证据快照"，
且冻结后内容与治理策略都不可变。
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .gold import GoldAnnotationService, GoldVersionService
from .gold_models import (
    AnnotationConflict,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
    TestAssetTaxonomy,
)
from .knowledge_models import KnowledgeAuditLog
from projects.models import Project, ProjectMember

BASE = "/api/knowledge-evolution"


class GoldGovernanceTestBase(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username="gov-lead", password="pass")
        self.executor = User.objects.create_user(username="gov-executor", password="pass")
        self.project = Project.objects.create(name="上证 e 投票", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")

        self.taxonomy = TestAssetTaxonomy.objects.create(
            project=self.project, scope_key="sse_evote", version="1.0.0",
            state="published", maintained_by=self.lead, approved_by=self.lead,
            categories=[{"key": "meeting", "name": "股东大会"}],
            critical_scenarios=[
                {"key": "vote_submit", "name": "投票提交"},
                {"key": "result_tally", "name": "结果统计"},
            ],
        )
        self.dataset = GoldDataset.objects.create(
            project=self.project, name="e 投票专用金标", task_type="testcase_generation",
            scope_type="domain", scope_key="sse_evote", taxonomy_version=self.taxonomy,
            owner=self.lead, created_by=self.lead,
        )
        self.version = GoldDatasetVersion.objects.create(
            dataset=self.dataset, version="v1", created_by=self.lead,
        )
        self.client = APIClient()

    def _case(self, *, source_hash="a" * 64) -> GoldCase:
        return GoldCase.objects.create(
            version=self.version, task_type="testcase_generation",
            title="投票提交用例", source_hash=source_hash,
            input_snapshot={"requirement": "股东大会投票"},
        )

    def _confirm(self, case, *, tags=("vote_submit", "result_tally"), split="gold"):
        payload = {
            "answer": {"case": "提交投票"},
            "rubric_scores": {"correctness": 1.0},
            "evidence": [{"doc": "需求 3.2"}],
            "conclusion": "accepted",
        }
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            comment="初标：与需求一致", tags=list(tags), split=split, **payload,
        )
        return GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            comment="复核通过", tags=list(tags), split=split, category="meeting",
            **payload,
        )


class GoldAnnotationGovernanceTests(GoldGovernanceTestBase):
    def test_review_records_governance_and_evidence_snapshot(self):
        case = self._case()
        review = self._confirm(case, tags=("vote_submit", "result_tally"), split="gold")
        case.refresh_from_db()

        self.assertEqual(case.state, "confirmed")
        # 人工结论必须落到样本上：分区与标签来自审核动作，而不是候选预检建议。
        self.assertEqual(case.split, "gold")
        self.assertEqual(case.tags, ["vote_submit", "result_tally"])
        self.assertEqual(review.split, "gold")
        self.assertEqual(review.category, "meeting")
        self.assertEqual(review.comment, "复核通过")
        snapshot = review.review_snapshot
        self.assertEqual(len(snapshot["input_hash"]), 64)
        self.assertEqual(len(snapshot["answer_hash"]), 64)
        self.assertEqual(snapshot["source_hash"], case.source_hash)

    def test_review_and_freeze_are_audited_with_actor_and_states(self):
        case = self._case()
        self._confirm(case)
        case.refresh_from_db()
        GoldVersionService.freeze(version=self.version, actor=self.lead)

        review_log = KnowledgeAuditLog.objects.get(
            entity_id=str(case.id), action="annotate", detail__round="review",
        )
        self.assertEqual(review_log.project_id, self.project.pk)
        self.assertEqual(review_log.actor_id, self.lead.pk)
        self.assertEqual(review_log.to_state, "confirmed")
        self.assertEqual(review_log.reason, "复核通过")

        freeze_log = KnowledgeAuditLog.objects.get(entity_id=str(self.version.id), action="freeze")
        self.assertEqual(freeze_log.actor_id, self.lead.pk)
        self.assertEqual(freeze_log.to_state, "frozen")
        self.assertEqual(freeze_log.project_id, self.project.pk)

    def test_conflict_arbitration_is_audited(self):
        case = self._case()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={"case": "A"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={"case": "B"}, rubric_scores={}, evidence=[], conclusion="rejected",
        )
        conflict = AnnotationConflict.objects.get(case=case)
        self.assertEqual(conflict.state, "open")
        self.assertEqual(case.refresh_from_db() or case.state, "conflict")

        GoldAnnotationService.resolve(
            conflict=conflict, actor=self.lead, answer={"case": "A"},
            rubric_scores={}, evidence=[], conclusion="accepted",
            comment="以初标为准", tags=["vote_submit"], split="regression",
        )
        case.refresh_from_db()
        self.assertEqual(case.state, "confirmed")
        self.assertEqual(case.split, "regression")
        log = KnowledgeAuditLog.objects.get(entity_id=str(case.id), action="conflict_resolved")
        self.assertEqual(log.actor_id, self.lead.pk)
        self.assertEqual(log.reason, "以初标为准")


class GoldFreezeGateTests(GoldGovernanceTestBase):
    def test_candidate_without_human_review_cannot_be_frozen(self):
        self._case()  # 只建候选，不做任何人工审核
        with self.assertRaisesMessage(ValidationError, "未确认"):
            GoldVersionService.freeze(version=self.version, actor=self.lead)
        self.version.refresh_from_db()
        self.assertNotEqual(self.version.state, "frozen")

    def test_open_conflict_blocks_freeze(self):
        case = self._case()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={"case": "A"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={"case": "B"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        self.assertTrue(AnnotationConflict.objects.filter(case=case, state="open").exists())
        # 冲突未解时样本状态是 conflict，会先被"未确认"拦下——两条门禁都成立。
        with self.assertRaises(ValidationError):
            GoldVersionService.freeze(version=self.version, actor=self.lead)

    def test_open_conflict_blocks_freeze_when_case_still_looks_confirmed(self):
        """只看 ``case.state`` 会漏掉"结论已定、争议未结"：冲突记录仍停在 open。"""
        case = self._case()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={"case": "A"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={"case": "B"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        case.refresh_from_db()
        case.state = "confirmed"
        case.save(update_fields=["state"])

        with self.assertRaisesMessage(ValidationError, "冲突未仲裁"):
            GoldVersionService.freeze(version=self.version, actor=self.lead)

    def test_missing_critical_scenario_blocks_freeze(self):
        case = self._case()
        self._confirm(case, tags=("vote_submit",))  # 只覆盖两个关键场景中的一个
        with self.assertRaisesMessage(ValidationError, "关键场景未覆盖") as ctx:
            GoldVersionService.freeze(version=self.version, actor=self.lead)
        self.assertIn("结果统计", str(ctx.exception))

    def test_full_coverage_allows_freeze(self):
        case = self._case()
        self._confirm(case, tags=("vote_submit", "result_tally"))
        version = GoldVersionService.freeze(version=self.version, actor=self.lead)
        self.assertEqual(version.state, "frozen")
        self.assertEqual(version.sample_stats["total"], 1)
        self.assertEqual(len(version.content_hash), 64)
        self.assertEqual(
            version.governance_snapshot["taxonomy_version_id"], str(self.taxonomy.id)
        )
        self.assertTrue(version.governance_snapshot["optimization_allowed"])

    def test_retired_taxonomy_blocks_freeze(self):
        case = self._case()
        self._confirm(case)
        self.taxonomy.state = "retired"
        self.taxonomy.save(update_fields=["state"])
        with self.assertRaisesMessage(ValidationError, "只有已发布的分类版本"):
            GoldVersionService.freeze(version=self.version, actor=self.lead)

    def test_domain_dataset_without_taxonomy_blocks_freeze(self):
        dataset = GoldDataset.objects.create(
            project=self.project, name="未绑定分类的专用集",
            task_type="testcase_generation", scope_type="domain",
            scope_key="sse_evote", created_by=self.lead,
        )
        version = GoldDatasetVersion.objects.create(
            dataset=dataset, version="v1", created_by=self.lead,
        )
        case = GoldCase.objects.create(
            version=version, task_type="testcase_generation", title="用例",
            source_hash="b" * 64, input_snapshot={},
        )
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        with self.assertRaisesMessage(ValidationError, "必须绑定已发布的分类版本"):
            GoldVersionService.freeze(version=version, actor=self.lead)

    def test_general_dataset_without_taxonomy_can_freeze(self):
        """通用数据集不强制绑定分类——否则存量流程会被新门禁一刀切挡死。"""
        dataset = GoldDataset.objects.create(
            project=self.project, name="通用金标", task_type="code_review",
            created_by=self.lead,
        )
        version = GoldDatasetVersion.objects.create(
            dataset=dataset, version="v1", created_by=self.lead,
        )
        case = GoldCase.objects.create(
            version=version, task_type="code_review", title="空值检查",
            source_hash="c" * 64, input_snapshot={},
        )
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        self.assertEqual(
            GoldVersionService.freeze(version=version, actor=self.lead).state, "frozen",
        )


class GoldFrozenImmutabilityTests(GoldGovernanceTestBase):
    def test_frozen_version_content_and_governance_are_immutable(self):
        case = self._case()
        self._confirm(case)
        version = GoldVersionService.freeze(version=self.version, actor=self.lead)

        version.governance_snapshot = {"scope_type": "general"}
        with self.assertRaisesMessage(ValidationError, "不可修改"):
            version.save()

        pristine = GoldDatasetVersion.objects.get(pk=version.pk)
        pristine.content_hash = "0" * 64
        with self.assertRaisesMessage(ValidationError, "不可修改"):
            pristine.save()

        case.refresh_from_db()
        case.title = "冻结后改标题"
        with self.assertRaises(ValidationError):
            case.save()

    def test_frozen_version_rejects_further_annotation(self):
        case = self._case()
        self._confirm(case)
        GoldVersionService.freeze(version=self.version, actor=self.lead)
        # 必须清掉 FK 缓存：否则 case.version 还是冻结前的对象，看不到 frozen 状态。
        case.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "冻结版本不能继续标注"):
            GoldAnnotationService.submit(
                case=case, round_name="review", actor=self.lead,
                answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
            )


class GoldGovernancePermissionTests(GoldGovernanceTestBase):
    def _conflicted_case(self):
        case = self._case()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={"case": "A"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={"case": "B"}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        return case

    def test_executor_cannot_do_review_round(self):
        case = self._case()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{BASE}/gold-cases/{case.id}/annotate/",
            {"round": "review", "answer": {}, "conclusion": "accepted"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_executor_cannot_arbitrate_conflict(self):
        case = self._conflicted_case()
        conflict = AnnotationConflict.objects.get(case=case)
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{BASE}/annotation-conflicts/{conflict.id}/resolve/",
            {"answer": {}, "conclusion": "accepted"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_executor_cannot_freeze_version(self):
        case = self._case()
        self._confirm(case)
        self.client.force_authenticate(self.executor)
        response = self.client.post(f"{BASE}/gold-dataset-versions/{self.version.id}/freeze/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_lead_can_review_and_executor_can_annotate_primary(self):
        case = self._case()
        self.client.force_authenticate(self.executor)
        primary = self.client.post(
            f"{BASE}/gold-cases/{case.id}/annotate/",
            {"round": "primary", "answer": {}, "conclusion": "accepted", "split": "gold"},
            format="json",
        )
        self.assertEqual(primary.status_code, status.HTTP_201_CREATED, primary.content)

        self.client.force_authenticate(self.lead)
        review = self.client.post(
            f"{BASE}/gold-cases/{case.id}/annotate/",
            {
                "round": "review", "answer": {}, "conclusion": "accepted",
                "split": "gold", "tags": ["vote_submit", "result_tally"],
                "category": "meeting", "comment": "复核通过",
            },
            format="json",
        )
        self.assertEqual(review.status_code, status.HTTP_201_CREATED, review.content)
        case.refresh_from_db()
        self.assertEqual(case.state, "confirmed")
        self.assertEqual(case.split, "gold")
        self.assertEqual(case.tags, ["vote_submit", "result_tally"])
