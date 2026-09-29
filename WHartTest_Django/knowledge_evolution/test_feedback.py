"""任务 10：标准 Feedback Event 服务测试。"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from projects.models import Project

from .feedback import FeedbackContext, FeedbackService
from .models import FeedbackEvent, GenerationOutput, RetrievalTrace


class FeedbackServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fb-user", password="p")
        self.project = Project.objects.create(name="Feedback Project", creator=self.user)
        self.trace = RetrievalTrace.objects.create(
            project=self.project,
            task_type="code_review",
            task_id="task-1",
            query="review",
            channels={},
            candidates=[],
            citations=[],
        )
        self.output = GenerationOutput.objects.create(
            project=self.project,
            trace=self.trace,
            task_type="code_review",
            task_id="task-1",
            content="发现 3 个问题",
            output_hash="abc",
        )

    def _ctx(self, **kwargs):
        return FeedbackContext(
            project_id=self.project.pk,
            output=self.output,
            trace=self.trace,
            actor=self.user,
            actor_type="user",
            task_type="code_review",
            task_id="task-1",
            **kwargs
        )

    def test_record_accepted(self):
        svc = FeedbackService(self._ctx())
        event = svc.record_accepted(reason="认可")
        self.assertEqual(event.signal, "accepted")
        self.assertEqual(event.value, 0.8)
        self.assertEqual(event.output_id, self.output.pk)

    def test_idempotency(self):
        svc = FeedbackService(self._ctx())
        e1 = svc.record_rejected("evidence_wrong", "证据不对")
        e2 = svc.record_rejected("evidence_wrong", "证据不对")
        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(FeedbackEvent.objects.filter(signal="rejected").count(), 1)

    def test_record_edited(self):
        svc = FeedbackService(self._ctx())
        event = svc.record_edited(before="A\nB", after="A\nC", reason="修正")
        self.assertEqual(event.signal, "edited")
        self.assertIn("before_hash", event.detail["edit"])
        self.assertEqual(event.detail["edit"]["diff_summary"]["lines_before"], 2)

    def test_record_test_result(self):
        svc = FeedbackService(self._ctx())
        event = svc.record_test_result(passed=False, details={"case": "c1"})
        self.assertEqual(event.signal, "test_failed")
        self.assertEqual(event.value, 0.9)

    def test_record_defect_and_merge(self):
        svc = FeedbackService(self._ctx())
        confirmed = svc.record_defect_status(confirmed=True, reason="确认缺陷")
        self.assertEqual(confirmed.signal, "defect_confirmed")
        merged = svc.record_merge(commit_sha="a1b2c3")
        self.assertEqual(merged.signal, "merged")

    def test_spam_cooldown(self):
        svc = FeedbackService(self._ctx())
        svc.record_accepted(reason="ok")
        svc2 = FeedbackService(self._ctx())
        spam, reason = svc2._is_spam("accepted")
        self.assertTrue(spam)
        self.assertIn("60s", reason)

    def test_record_for_output(self):
        event = FeedbackService.record_for_output(
            output=self.output,
            signal="false_positive",
            actor=self.user,
            reason_code="user_reported",
            comment="误报",
        )
        self.assertEqual(event.signal, "false_positive")
        self.assertEqual(event.value, 0.8)
