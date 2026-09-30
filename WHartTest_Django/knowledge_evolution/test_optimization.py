from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from projects.models import Project

from .optimization import OptimizationProposalService
from .trace_models import FailureAttribution


User = get_user_model()


class OptimizationProposalServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="optimizer-user", password="pass")
        self.project = Project.objects.create(name="Optimizer Project", creator=self.user)

    def _attribution(self, category, state="confirmed"):
        return FailureAttribution.objects.create(
            project=self.project, category=category, source="rule", confidence=1.0,
            hypothesis=f"{category} hypothesis", state=state,
            fingerprint=f"{category}-{state}", confirmed_by=self.user if state == "confirmed" else None,
        )

    def test_groups_confirmed_attributions_into_four_safe_proposal_types(self):
        items = [
            self._attribution("prompt_error"),
            self._attribution("knowledge_missing"),
            self._attribution("retrieval_error"),
            self._attribution("tool_error"),
        ]
        proposals = OptimizationProposalService.generate(attributions=items, actor=self.user)
        self.assertEqual({item.proposal_type for item in proposals}, {
            "prompt", "knowledge", "retrieval_policy", "skill_tool",
        })
        self.assertTrue(all(item.state == "draft" for item in proposals))
        self.assertTrue(all("尚未通过" in item.risk_notes[0] for item in proposals))

    def test_generation_is_idempotent(self):
        attribution = self._attribution("prompt_error")
        first = OptimizationProposalService.generate(attributions=[attribution], actor=self.user)[0]
        second = OptimizationProposalService.generate(attributions=[attribution], actor=self.user)[0]
        self.assertEqual(first.id, second.id)

    def test_unconfirmed_attribution_is_rejected(self):
        with self.assertRaises(ValidationError):
            OptimizationProposalService.generate(
                attributions=[self._attribution("tool_error", state="proposed")], actor=self.user,
            )
