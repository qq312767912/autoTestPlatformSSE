"""T04：自动候选事件、预检与补偿。

测试组织原则与 T09–T13 一致：**每个用例只证明一件在真实业务里会出错的事**。
这里的四件事分别是——去重不能跨项目、重放不能重复建候选、缺证据不能进审核队列、
失败必须留痕并可重试到死信。
"""
from __future__ import annotations

import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.feedback import FeedbackService
from knowledge_evolution.gold import AssetCandidateService
from knowledge_evolution.gold_models import GoldCase
from knowledge_evolution.models import (
    AssetCandidateEvent,
    FeedbackEvent,
    GenerationOutput,
    RetrievalTrace,
)
from knowledge_evolution.operations import WorkflowGateService
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT, SkillHubBaseTests


def _feedback(output, *, signal="defect_confirmed", expected=None, evidence=None, key="fb-1"):
    detail = {"expected_output": expected} if expected is not None else {}
    return FeedbackEvent.objects.create(
        project=output.project,
        output=output,
        trace=output.trace,
        signal=signal,
        value=1.0,
        actor_type="user",
        evidence=evidence or [],
        detail=detail,
        idempotency_key=key,
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AssetCandidateServiceTests(SkillHubBaseTests):
    def _stage_output(self, *, project=None, workflow_id="wf-cand", task_type="testcase_generation"):
        output = self.make_output(task_type=task_type, project=project, workflow_id=workflow_id)
        WorkflowGateService.register_output(output)
        return output

    def _event_for_output(self, output):
        return AssetCandidateEvent.objects.get(source_type="stage_output", source_id=str(output.pk))

    # ------------------------------------------------------------------ 入队

    def test_formal_stage_output_becomes_candidate_pending_manual_review(self):
        output = self._stage_output()

        event = self._event_for_output(output)
        self.assertEqual(event.status, "pending")

        event = AssetCandidateService.process(event)

        self.assertEqual(event.status, "needs_review")
        case = event.candidate
        self.assertIsNotNone(case)
        # 自动化只允许产出候选：任何"高置信自动确认"都不实现。
        self.assertEqual(case.state, "candidate")
        self.assertEqual(case.task_type, "testcase_generation")
        self.assertEqual(case.candidate_origin, "feedback")
        self.assertEqual(len(case.dedup_fingerprint), 64)
        self.assertEqual(case.dedup_fingerprint, event.preflight["dedup_fingerprint"])
        # 自动建议与人工最终结论必须是两套字段：tags 只写人工确认后的标签。
        self.assertEqual(case.recommended_split, "fresh")
        self.assertEqual(case.tags, [])

    def test_single_capability_output_is_not_a_workflow_candidate(self):
        output = self.make_output(task_type="case_review")

        WorkflowGateService.register_output(output)

        self.assertFalse(AssetCandidateEvent.objects.exists())

    def test_recorded_feedback_enqueues_a_candidate_event(self):
        output = self.make_output(task_type="testcase_generation", workflow_id="wf-hook")

        feedback = FeedbackService.record_for_output(
            output=output, signal="test_failed", actor=self.executor,
        )

        event = AssetCandidateEvent.objects.get(source_type="feedback", source_id=str(feedback.pk))
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.payload["output_id"], str(output.pk))

    # ------------------------------------------------------------------ 去重

    def test_same_input_and_expectation_merge_into_one_candidate(self):
        output = self.make_output(task_type="testcase_generation")
        expected = {"cases": ["登录失败提示"]}
        first = _feedback(output, expected=expected, evidence=[{"ref": "bug-1"}], key="dup-1")
        second = _feedback(output, expected=expected, evidence=[{"ref": "bug-2"}], key="dup-2")

        AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(first))
        event = AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(second))

        cases = GoldCase.objects.filter(version__dataset__project=self.project)
        self.assertEqual(cases.count(), 1)
        case = cases.first()
        self.assertEqual(event.candidate_id, case.pk)
        self.assertEqual(case.review_checklist["occurrences"], 2)
        self.assertEqual({item["ref"] for item in case.evidence}, {"bug-1", "bug-2"})

    def test_replaying_the_same_event_does_not_create_a_second_candidate(self):
        output = self.make_output(task_type="testcase_generation")
        feedback = _feedback(output, expected={"cases": ["x"]}, evidence=[{"ref": "r"}], key="replay-1")
        event = AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(feedback))

        replayed = AssetCandidateService.process(event)

        self.assertEqual(replayed.pk, event.pk)
        self.assertEqual(replayed.attempts, 1)
        self.assertEqual(GoldCase.objects.filter(version__dataset__project=self.project).count(), 1)

    def test_conflicting_expectations_become_arbitration_instead_of_overwrite(self):
        output = self.make_output(task_type="testcase_generation")
        first = _feedback(output, expected={"cases": ["a"]}, evidence=[{"ref": "r1"}], key="conf-1")
        second = _feedback(output, expected={"cases": ["b"]}, evidence=[{"ref": "r2"}], key="conf-2")

        AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(first))
        event = AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(second))

        case = event.candidate
        self.assertEqual(GoldCase.objects.filter(version__dataset__project=self.project).count(), 1)
        self.assertEqual(case.state, "conflict")
        # 旧结论不被最新上传覆盖：原期望保持不动，只留下冲突记录。
        self.assertEqual(case.expected_output, {"cases": ["a"]})
        conflict = case.review_checklist["conflicts"][0]
        self.assertEqual(conflict["against_case_id"], str(case.pk))
        self.assertIn("人工仲裁", conflict["reason"])

    def test_dedup_never_crosses_projects(self):
        expected = {"cases": ["x"]}
        foreign_output = self.make_output(task_type="testcase_generation", project=self.other_project)
        foreign = _feedback(
            foreign_output, expected=expected, evidence=[{"ref": "b"}], key="xp-1",
        )
        foreign_event = AssetCandidateService.process(
            AssetCandidateService.enqueue_from_feedback(foreign)
        )
        foreign_case = foreign_event.candidate

        local_version = AssetCandidateService.candidate_bucket(
            project=self.project, task_type="testcase_generation",
        )
        matched, relationship = AssetCandidateService._find_existing(
            project=self.project, task_type="testcase_generation", version=local_version,
            source_hash="not-an-existing-hash", fingerprint=foreign_case.dedup_fingerprint,
            input_snapshot=foreign_case.input_snapshot, expected=expected,
        )

        self.assertIsNone(matched)
        self.assertEqual(relationship, "new")

    # ------------------------------------------------------------------ 预检

    def test_defect_candidate_without_evidence_stays_out_of_the_review_queue(self):
        output = self.make_output(task_type="testcase_generation")
        feedback = _feedback(
            output, signal="defect_confirmed", expected={"cases": ["x"]}, evidence=[],
            key="no-evidence",
        )

        event = AssetCandidateService.process(AssetCandidateService.enqueue_from_feedback(feedback))

        # 候选保留、缺件清单可见，但不进审核队列——否则审核人会拿到一条无法复核的样本。
        self.assertEqual(event.status, "completed")
        self.assertIn("source_evidence", event.preflight["blocking"])
        self.assertIn("rubric", event.preflight["missing"])
        self.assertEqual(event.candidate.state, "candidate")

    def test_prohibited_privacy_candidate_cannot_be_used_for_optimization(self):
        output = self.make_output(task_type="testcase_generation", workflow_id="wf-privacy")
        protocol = dict(output.metadata["protocol"])
        protocol["privacy"] = {"level": "prohibited"}
        output.metadata = {"protocol": protocol}
        output.save(update_fields=["metadata"])

        WorkflowGateService.register_output(output)
        event = AssetCandidateService.process(self._event_for_output(output))

        self.assertTrue(event.preflight["privacy"]["prohibited"])
        self.assertEqual(event.candidate.privacy_level, "prohibited")
        self.assertFalse(event.candidate.allow_optimization)

    def test_cross_project_source_reference_fails_instead_of_being_recorded(self):
        foreign = self.make_output(task_type="testcase_generation", project=self.other_project)
        event = AssetCandidateService._enqueue(
            project=self.project, source_type="feedback", source_id=str(foreign.pk),
            signal="edited",
            payload={"output_id": str(foreign.pk), "task_type": "testcase_generation"},
            idempotency_key="cross-project-probe",
        )

        event = AssetCandidateService.process(event)

        self.assertEqual(event.status, "failed")
        self.assertIn("不属于当前项目", event.last_error)
        self.assertIsNone(event.candidate)

    # ------------------------------------------------------------------ 补偿

    def test_processing_failure_is_retriable_until_dead_letter(self):
        missing = str(uuid.uuid4())
        event = AssetCandidateService._enqueue(
            project=self.project, source_type="feedback", source_id=missing, signal="edited",
            payload={"output_id": missing, "task_type": "testcase_generation"},
            idempotency_key="dead-letter-probe",
        )

        event = AssetCandidateService.process(event)
        self.assertEqual((event.status, event.attempts), ("failed", 1))

        event = AssetCandidateService.retry(event)
        self.assertEqual((event.status, event.attempts), ("failed", 2))

        event = AssetCandidateService.retry(event)
        self.assertEqual((event.status, event.attempts), ("dead_letter", 3))

        summary = AssetCandidateService.status_summary(self.project.pk)
        self.assertEqual(summary["dead_letter"], 1)
        self.assertTrue(summary["alert"])

    def test_batch_retry_replays_failed_events_within_the_project(self):
        missing = str(uuid.uuid4())
        AssetCandidateService.process(AssetCandidateService._enqueue(
            project=self.project, source_type="feedback", source_id=missing, signal="edited",
            payload={"output_id": missing, "task_type": "testcase_generation"},
            idempotency_key="batch-probe",
        ))
        foreign_missing = str(uuid.uuid4())
        AssetCandidateService.process(AssetCandidateService._enqueue(
            project=self.other_project, source_type="feedback", source_id=foreign_missing,
            signal="edited",
            payload={"output_id": foreign_missing, "task_type": "testcase_generation"},
            idempotency_key="batch-probe-foreign",
        ))

        results = AssetCandidateService.retry_failed(project_id=self.project.pk)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "failed")


class AssetCandidateAPITests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from projects.models import Project, ProjectMember

        self.lead = User.objects.create_user(username="cand-lead", password="pass")
        self.member = User.objects.create_user(username="cand-member", password="pass")
        self.outsider = User.objects.create_user(username="cand-outsider", password="pass")
        self.project = Project.objects.create(name="候选项目", creator=self.lead)
        self.other_project = Project.objects.create(name="他项目", creator=self.lead)
        ProjectMember.objects.create(project=self.project, user=self.lead, role="owner")
        ProjectMember.objects.create(project=self.project, user=self.member, role="member")
        ProjectMember.objects.create(project=self.other_project, user=self.lead, role="owner")
        self.client = APIClient()
        self.url = "/api/knowledge-evolution/asset-candidates/"

        trace = RetrievalTrace.objects.create(
            project=self.project, task_type="testcase_generation", query="生成用例",
        )
        output = GenerationOutput.objects.create(
            project=self.project, trace=trace, task_type="testcase_generation",
            task_id="t-1", content="正文", output_hash="a" * 64,
            metadata={"protocol": {"stage": "testcase_generation", "workflow_id": "wf-api"}},
        )
        self.event = AssetCandidateService.enqueue_from_output(output)

    def test_member_only_sees_this_projects_candidates(self):
        AssetCandidateEvent.objects.create(
            project=self.other_project, source_type="feedback", source_id="other",
            signal="edited", idempotency_key="api-foreign",
        )
        self.client.force_authenticate(self.member)

        listing = self.client.get(self.url)

        self.assertEqual(listing.status_code, 200, listing.content)
        ids = [item["id"] for item in listing.json()["data"]]
        self.assertEqual(ids, [str(self.event.pk)])

    def test_stats_and_retry_are_project_scoped(self):
        self.client.force_authenticate(self.member)
        # 模拟"派发失败"：事件停在 failed，正是 retry 接口要处理的场景。
        AssetCandidateEvent.objects.filter(pk=self.event.pk).update(status="failed", attempts=1)

        before = self.client.get(f"{self.url}stats/", {"project": self.project.pk})
        self.assertEqual(before.status_code, 200, before.content)
        self.assertEqual(before.json()["data"]["failed"], 1)
        self.assertTrue(before.json()["data"]["alert"])

        retried = self.client.post(
            f"{self.url}retry/", {"project": self.project.pk}, format="json",
        )
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual(retried.json()["data"]["retried"], 1)

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "needs_review")

        after = self.client.get(f"{self.url}stats/", {"project": self.project.pk})
        self.assertEqual(after.json()["data"]["needs_review"], 1)

    def test_non_member_cannot_read_stats_of_a_project(self):
        self.client.force_authenticate(self.outsider)

        response = self.client.get(f"{self.url}stats/", {"project": self.project.pk})

        self.assertEqual(response.status_code, 403)

    def test_flywheel_operations_exposes_candidate_alerts(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(
            "/api/knowledge-evolution/operations/candidate-alerts/",
            {"project": self.project.pk},
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["pending"], 1)
