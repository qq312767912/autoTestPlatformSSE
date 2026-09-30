from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from projects.models import Project, ProjectMember
from rest_framework import status
from rest_framework.test import APIClient

from .gold import GoldAnnotationService, GoldCandidateService, GoldVersionService
from .gold_evaluation import GoldEvaluationBridge
from .gold_models import AnnotationConflict, GoldCase, GoldDataset, GoldDatasetVersion
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


User = get_user_model()


class GoldAssetServiceTest(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username="gold-lead", password="pass")
        self.executor = User.objects.create_user(username="gold-executor", password="pass")
        self.project = Project.objects.create(name="Gold Project", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.dataset = GoldDataset.objects.create(
            project=self.project, name="代码审查金标", task_type="code_review",
            owner=self.lead, created_by=self.lead,
        )
        self.version = GoldDatasetVersion.objects.create(
            dataset=self.dataset, version="v1", created_by=self.lead,
        )
        self.trace = RetrievalTrace.objects.create(
            project=self.project, user=self.executor, task_type="code_review",
            task_id="review-1", query="sensitive diff body",
        )
        self.output = GenerationOutput.objects.create(
            project=self.project, trace=self.trace, task_type="code_review",
            task_id="review-1", content="review result", output_hash="a" * 64,
            metadata={"protocol": {"schema_version": "platform-output/v1", "evidence": [{"file": "a.py", "line": 10}]}},
        )
        self.feedback = FeedbackEvent.objects.create(
            project=self.project, output=self.output, trace=self.trace,
            signal="defect_confirmed", reason_code="null-check", comment="确认缺陷",
            idempotency_key="gold-feedback-1", actor=self.executor,
        )

    def _candidate(self):
        return GoldCandidateService.from_feedback(
            version=self.version, feedback=self.feedback, actor=self.executor,
        )

    def test_feedback_creates_idempotent_candidate_without_raw_query(self):
        first = self._candidate()
        second = self._candidate()
        self.assertEqual(first.id, second.id)
        self.assertNotIn("sensitive diff body", str(first.input_snapshot))
        self.assertEqual(first.input_snapshot["output_hash"], self.output.output_hash)
        self.version.refresh_from_db()
        self.assertEqual(self.version.state, "labeling")

    def test_matching_dual_annotations_confirm_and_freeze(self):
        case = self._candidate()
        payload = {
            "answer": {"defect": "缺少空值检查"},
            "rubric_scores": {"correctness": 1.0},
            "evidence": [{"file": "a.py", "line": 10}],
            "conclusion": "accepted",
        }
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor, **payload,
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead, **payload,
        )
        case.refresh_from_db()
        self.assertEqual(case.state, "confirmed")
        version = GoldVersionService.freeze(version=self.version, actor=self.lead)
        self.assertEqual(version.state, "frozen")
        self.assertEqual(version.sample_stats["total"], 1)
        self.assertEqual(len(version.content_hash), 64)

        case.title = "禁止修改"
        with self.assertRaises(ValidationError):
            case.save()

        suite = GoldEvaluationBridge.materialize(version=version, actor=self.lead)
        self.assertEqual(suite.suite_type, "gold")
        self.assertEqual(suite.cases.count(), 1)
        eval_case = suite.cases.get()
        self.assertEqual(eval_case.metadata["gold_version_hash"], version.content_hash)

    def test_conflict_requires_arbitration(self):
        case = self._candidate()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={"defect": True}, rubric_scores={"correctness": 1}, evidence=[],
            conclusion="accepted",
        )
        GoldAnnotationService.submit(
            case=case, round_name="review", actor=self.lead,
            answer={"defect": False}, rubric_scores={"correctness": 0}, evidence=[],
            conclusion="rejected",
        )
        case.refresh_from_db()
        self.assertEqual(case.state, "conflict")
        conflict = AnnotationConflict.objects.get(case=case)
        GoldAnnotationService.resolve(
            conflict=conflict, actor=self.lead, answer={"defect": True},
            rubric_scores={"correctness": 1}, evidence=[], conclusion="accepted",
        )
        case.refresh_from_db(); conflict.refresh_from_db()
        self.assertEqual(case.state, "confirmed")
        self.assertEqual(conflict.state, "resolved")

    def test_reviewer_must_differ_from_primary_annotator(self):
        case = self._candidate()
        GoldAnnotationService.submit(
            case=case, round_name="primary", actor=self.executor,
            answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
        )
        with self.assertRaises(ValidationError):
            GoldAnnotationService.submit(
                case=case, round_name="review", actor=self.executor,
                answer={}, rubric_scores={}, evidence=[], conclusion="accepted",
            )

    def test_cannot_freeze_unconfirmed_cases(self):
        self._candidate()
        with self.assertRaises(ValidationError):
            GoldVersionService.freeze(version=self.version, actor=self.lead)


class GoldAssetApiPermissionTest(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username="api-lead", password="pass")
        self.executor = User.objects.create_user(username="api-executor", password="pass")
        self.project = Project.objects.create(name="Gold API Project", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="admin")
        ProjectMember.objects.create(project=self.project, user=self.executor, role="member")
        self.client = APIClient()

    def test_only_lead_can_create_dataset(self):
        payload = {"project": self.project.id, "name": "API金标", "task_type": "case_review"}
        self.client.force_authenticate(self.executor)
        denied = self.client.post("/api/knowledge-evolution/gold-datasets/", payload, format="json")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.lead)
        created = self.client.post("/api/knowledge-evolution/gold-datasets/", payload, format="json")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

    def test_executor_cannot_freeze_version(self):
        dataset = GoldDataset.objects.create(
            project=self.project, name="冻结权限", task_type="case_review", created_by=self.lead,
        )
        version = GoldDatasetVersion.objects.create(dataset=dataset, version="v1", created_by=self.lead)
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"/api/knowledge-evolution/gold-dataset-versions/{version.id}/freeze/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
