from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase

from file_management.models import FileAsset
from projects.models import Project, ProjectMember

from .history_ingestion import HistoryIngestionService, HistoryReplayService
from .history_models import HistoryImportBatch, HistoryReplayDifference
from .workflow_models import FlywheelRun


class HistoryFlywheelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("history-lead", password="x")
        self.project = Project.objects.create(name="history-project", creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")

    def _file(self, project, name, content):
        return FileAsset.objects.create(
            project=project, owner=self.user, file=SimpleUploadedFile(name, content),
            original_name=name, extension="txt", mime_type="text/plain", size=len(content),
            sha256=__import__("hashlib").sha256(content).hexdigest(), status="available",
        )

    def _manifest(self):
        files = [
            ("requirement", self._file(self.project, "requirement.txt", b"requirement")),
            ("plan", self._file(self.project, "plan.txt", b"plan")),
            ("case", self._file(self.project, "case.txt", b"case")),
        ]
        return {"name": "v1", "task_type": "testcase_generation",
                "files": [{"role": role, "file_id": item.pk} for role, item in files]}

    def test_preflight_is_read_only_and_confirm_is_idempotent(self):
        result = HistoryIngestionService.preflight(project=self.project, manifest=self._manifest())
        self.assertTrue(result["ok"])
        self.assertEqual(HistoryImportBatch.objects.count(), 0)
        with patch("knowledge_evolution.gold.AssetCandidateService.dispatch"):
            batch, created = HistoryIngestionService.confirm(
                project=self.project, token=result["confirmation_token"], actor=self.user,
            )
            replayed, created_again = HistoryIngestionService.confirm(
                project=self.project, token=result["confirmation_token"], actor=self.user,
            )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(batch.pk, replayed.pk)
        self.assertEqual(batch.items.count(), 3)
        self.assertEqual(batch.candidate_count, 2)

    def test_preflight_never_exposes_cross_project_file(self):
        foreign = Project.objects.create(name="foreign-history", creator=self.user)
        file = self._file(foreign, "secret.txt", b"secret body")
        result = HistoryIngestionService.preflight(project=self.project, manifest={
            "files": [{"role": "requirement", "file_id": file.pk},
                      {"role": "plan", "file_id": file.pk},
                      {"role": "case", "file_id": file.pk}],
        })
        self.assertFalse(result["ok"])
        self.assertNotIn("secret body", str(result))

    def test_replay_is_isolated_and_uncertain_requires_human_decision(self):
        batch = HistoryImportBatch.objects.create(
            project=self.project, name="ready", status="completed", manifest={},
            manifest_hash="a" * 64, created_by=self.user,
        )
        replay = HistoryReplayService.create(
            project=self.project, batch=batch, actor=self.user, workflow_id="history-only",
            config={"model": "fixed"},
        )
        self.assertEqual(replay.flywheel_run.intent, "history_replay")
        self.assertTrue(replay.execution_lock["isolated"])
        replay = HistoryReplayService.record(replay=replay, rows=[
            {"case_key": "1", "expected": {"a": 1}, "actual": {"a": 1}},
            {"case_key": "2", "expected": ["a"], "actual": ["b"]},
        ])
        self.assertEqual(replay.status, "needs_review")
        diff = replay.differences.get(category="uncertain")
        HistoryReplayService.decide(difference=diff, actor=self.user, decision="acceptable")
        replay.refresh_from_db()
        self.assertEqual(replay.status, "passed")

    def test_critical_regression_blocks_gate_and_production_id_is_rejected(self):
        batch = HistoryImportBatch.objects.create(
            project=self.project, name="ready2", status="completed", manifest={},
            manifest_hash="b" * 64, created_by=self.user,
        )
        FlywheelRun.objects.create(
            project=self.project, workflow_id="production-id", entry_type="flywheel",
            intent="production", created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            HistoryReplayService.create(
                project=self.project, batch=batch, actor=self.user, workflow_id="production-id",
            )
        replay = HistoryReplayService.create(
            project=self.project, batch=batch, actor=self.user, workflow_id="history-critical",
        )
        replay = HistoryReplayService.record(replay=replay, rows=[
            {"case_key": "critical", "expected": {"x": 1}, "actual": {}, "critical": True},
        ])
        self.assertEqual(replay.status, "blocked")
        self.assertFalse(replay.gate_report["passed"])
