from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from projects.models import Project

from .capabilities import CapabilityReleaseService
from .knowledge_models import KnowledgeCandidate
from .optimization import (
    OptimizationMaterializationService,
    OptimizationProposalService,
    PromptOptimizer,
)
from .retrieval_models import RetrievalPolicy
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

    def _proposal(self, category, *, baseline_config=None):
        proposal_type = {
            "prompt_error": "prompt", "knowledge_missing": "knowledge",
            "retrieval_error": "retrieval_policy", "tool_error": "skill_tool",
        }[category]
        baseline = CapabilityReleaseService.create(
            project=self.project,
            kind="skill" if proposal_type == "skill_tool" else proposal_type,
            name=f"baseline-{proposal_type}", version=f"base-{proposal_type}",
            config=baseline_config or {}, actor=self.user,
        )
        return OptimizationProposalService.generate(
            attributions=[self._attribution(category)], actor=self.user,
            baseline_release=baseline,
        )[0]

    def test_prompt_optimizer_creates_append_only_candidate_release(self):
        from langgraph_integration.models import LLMConfig
        LLMConfig.objects.create(
            config_name="optimizer", name="model", api_url="http://example.com/v1", is_active=True,
        )
        proposal = self._proposal("prompt_error", baseline_config={"prompt": "保留原要求"})

        class Response:
            content = '{"candidate_prompt":"保留原要求\\n新增反证校验",' \
                      '"changes":["add counterevidence"],"risks":[]}'

        class LLM:
            def invoke(self, _prompt): return Response()

        release = OptimizationMaterializationService.materialize(
            proposal=proposal, actor=self.user,
            prompt_optimizer=PromptOptimizer(llm_factory=lambda _config: LLM()),
        )
        self.assertEqual(release.kind, "prompt")
        self.assertIn("新增反证校验", release.config["prompt"])
        self.assertEqual(release.state, "draft")

    def test_knowledge_optimizer_creates_review_candidate_not_production_asset(self):
        proposal = self._proposal("knowledge_missing")
        release = OptimizationMaterializationService.materialize(
            proposal=proposal, actor=self.user,
        )
        candidate = KnowledgeCandidate.objects.get(pk=release.config["candidate_id"])
        self.assertEqual(candidate.state, "pending")
        self.assertEqual(candidate.origin, "evaluation_failure")
        self.assertIsNone(candidate.promoted_asset_id)

    def test_retrieval_optimizer_creates_inactive_versioned_policy(self):
        proposal = self._proposal("retrieval_error", baseline_config={
            "sources": {"graph": {"enabled": False, "k": 500}},
        })
        release = OptimizationMaterializationService.materialize(
            proposal=proposal, actor=self.user,
        )
        policy = RetrievalPolicy.objects.get(pk=release.config["policy_id"])
        self.assertFalse(policy.is_active)
        self.assertFalse(policy.is_default)
        self.assertEqual(policy.config["sources"]["graph"]["k"], 200)
        self.assertTrue(policy.config["counterevidence_filter"])

    def test_skill_optimizer_whitelists_and_bounds_configuration(self):
        proposal = self._proposal("tool_error", baseline_config={"timeout_seconds": 20})
        proposal.change_patch = {"config": {
            "timeout_seconds": 999, "max_retries": 99,
            "validate_input": True, "validate_output": True,
        }}
        proposal.save(update_fields=["change_patch"])
        release = OptimizationMaterializationService.materialize(
            proposal=proposal, actor=self.user,
        )
        self.assertEqual(release.kind, "skill")
        self.assertEqual(release.config["timeout_seconds"], 300)
        self.assertEqual(release.config["max_retries"], 5)

    def test_skill_optimizer_rejects_arbitrary_code_configuration(self):
        proposal = self._proposal("tool_error")
        proposal.change_patch = {"config": {"python_code": "import os"}}
        proposal.save(update_fields=["change_patch"])
        with self.assertRaises(ValidationError):
            OptimizationMaterializationService.materialize(
                proposal=proposal, actor=self.user,
            )
