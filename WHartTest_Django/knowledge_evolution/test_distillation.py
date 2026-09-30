from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project

from .distillation import ExperienceDistiller
from .feedback import FeedbackService
from .models import GenerationOutput, KnowledgeCandidate, RetrievalTrace


class ExperienceDistillerTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="distillation-test")
        self.user = get_user_model().objects.create_user(username="distiller", password="x")

    def _feedback(self, task_id, signal="rejected", reason="evidence_wrong", comment=""):
        trace = RetrievalTrace.objects.create(
            project=self.project,
            user=self.user,
            task_type="knowledge_query",
            task_id=task_id,
            query=f"query-{task_id}",
        )
        output = GenerationOutput.objects.create(
            project=self.project,
            trace=trace,
            task_type="knowledge_query",
            task_id=task_id,
            content=f"answer-{task_id}",
            output_hash=(task_id * 64)[:64],
        )
        return FeedbackService.record_for_output(
            output=output,
            signal=signal,
            actor_type="integration",
            reason_code=reason,
            comment=comment,
        )

    def test_three_distinct_subjective_tasks_create_candidate(self):
        for task_id in ("a", "b", "c"):
            self._feedback(task_id)
        result = ExperienceDistiller(
            project_id=self.project.id,
            task_type="knowledge_query",
        ).run()
        self.assertEqual(len(result.created), 1)
        candidate = result.created[0]
        self.assertEqual(candidate.kind, "experience")
        self.assertEqual(candidate.origin, "distillation")
        self.assertEqual(candidate.state, "pending")
        self.assertEqual(candidate.payload["statistics"]["distinct_task_count"], 3)

    def test_two_objective_tasks_create_candidate(self):
        self._feedback("a", signal="false_positive")
        self._feedback("b", signal="false_positive")
        result = ExperienceDistiller(
            project_id=self.project.id,
            task_type="knowledge_query",
        ).run()
        self.assertEqual(len(result.created), 1)

    def test_single_feedback_does_not_create_candidate(self):
        self._feedback("a")
        result = ExperienceDistiller(
            project_id=self.project.id,
            task_type="knowledge_query",
        ).run()
        self.assertEqual(result.created, ())
        self.assertEqual(KnowledgeCandidate.objects.count(), 0)

    def test_is_idempotent_and_refreshes_pending_candidate(self):
        for task_id in ("a", "b", "c"):
            self._feedback(task_id)
        distiller = ExperienceDistiller(project_id=self.project.id, task_type="knowledge_query")
        first = distiller.run()
        second = distiller.run()
        self.assertEqual(len(first.created), 1)
        self.assertEqual(len(second.updated), 1)
        self.assertEqual(KnowledgeCandidate.objects.count(), 1)

    def test_sensitive_comment_is_redacted_and_raw_content_is_not_copied(self):
        for task_id in ("a", "b", "c"):
            self._feedback(task_id, comment="token=secret-value 联系 a@example.com")
        candidate = ExperienceDistiller(
            project_id=self.project.id,
            task_type="knowledge_query",
        ).run().created[0]
        serialized = str(candidate.payload)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("a@example.com", serialized)
        self.assertNotIn("answer-a", serialized)

    def test_api_triggers_distillation_for_authorized_project(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        for task_id in ("a", "b", "c"):
            self._feedback(task_id)
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            "/api/knowledge-evolution/knowledge-candidates/distill-feedback/",
            {"project": self.project.id, "task_type": "knowledge_query"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.data.get("data", response.data)
        self.assertEqual(payload["created_count"], 1)
