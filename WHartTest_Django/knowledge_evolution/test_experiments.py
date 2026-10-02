from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from .capabilities import CapabilityReleaseService
from .experiments import OptimizationExperimentService, RetrievalPolicyReplayExecutor
from .gold_models import GoldCase, GoldDataset, GoldDatasetVersion
from .optimization_models import OptimizationProposal
from .retrieval_models import RetrievalPolicy


class OptimizationExperimentServiceTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="race-user", password="x")
        self.project = Project.objects.create(name="Candidate Race", creator=self.user)
        dataset = GoldDataset.objects.create(
            project=self.project, name="review-gold", task_type="code_review", owner=self.user,
        )
        self.version = GoldDatasetVersion.objects.create(
            dataset=dataset, version="v1", state="draft",
        )
        for number in range(2):
            GoldCase.objects.create(
                version=self.version, task_type="code_review", title=f"case-{number}",
                input_snapshot={"query": f"q-{number}"},
                expected_output={"expected_output": "空指针"},
                required_items=["空指针"], split="gold", state="confirmed",
                source_hash=f"hash-{number}", created_by=self.user,
            )
        GoldDatasetVersion.objects.filter(pk=self.version.pk).update(
            state="frozen", content_hash="d" * 64, sample_stats={"case_count": 2},
            frozen_by=self.user,
        )
        self.version.refresh_from_db()
        baseline_release = CapabilityReleaseService.create(
            project=self.project, kind="prompt", name="review", version="v1", config={"prompt": "old"},
        )
        self.candidate_release = CapabilityReleaseService.create(
            project=self.project, kind="prompt", name="review", version="v2", config={"prompt": "new"},
        )
        self.proposal = OptimizationProposal.objects.create(
            project=self.project, baseline_release=baseline_release, proposal_type="prompt",
            title="prompt candidate", summary="fix", change_patch={}, fingerprint="e" * 64,
        )

    def test_frozen_gold_race_persists_runs_and_full_gate(self):
        experiment = OptimizationExperimentService.run(
            proposal=self.proposal, gold_version=self.version,
            baseline_executor=lambda _input: {"output": "无结论", "token_usage": 100},
            candidate_executor=lambda _input: {"output": "空指针", "token_usage": 100},
            candidate_release=self.candidate_release, actor=self.user,
        )
        self.assertEqual(experiment.status, "passed")
        self.assertEqual(experiment.baseline_run.replay_hash, self.version.content_hash)
        self.assertEqual(experiment.candidate_run.gold_dataset_version_id, self.version.id)
        for level in ("l0", "l1", "l2", "l3"):
            self.assertIn(f"no_{level}_regression", experiment.gate_report["hard_gates"])
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.state, "awaiting_approval")

    def test_retrieval_policy_executor_binds_policy_to_replay(self):
        policy = RetrievalPolicy.objects.create(
            project=self.project, name="candidate", version=1,
            config={"marker": "candidate"}, created_by=self.user,
        )

        class Orchestrator:
            def retrieve(self, request):
                return {
                    "evidence_package": [{"citation_id": request.policy.config["marker"]}],
                    "latency_ms": 3, "token_estimate": 8,
                }

        executor = RetrievalPolicyReplayExecutor(
            policy=policy, orchestrator=Orchestrator(),
            answer_generator=lambda input_payload, evidence: {"output": input_payload["query"]},
        )
        result = executor({"query": "review"})
        self.assertEqual(result["citations"], ["candidate"])
        self.assertEqual(result["retrieval_policy_id"], str(policy.id))

    def test_full_race_requires_and_reports_all_five_partitions(self):
        GoldDatasetVersion.objects.filter(pk=self.version.pk).update(state="draft")
        self.version.refresh_from_db()
        existing = set(self.version.cases.values_list("split", flat=True))
        for index, split in enumerate(("gold", "regression", "fresh", "challenge", "hidden")):
            if split in existing:
                continue
            GoldCase.objects.create(
                version=self.version, task_type="code_review", title=f"{split}-case",
                input_snapshot={"query": split}, expected_output={"expected_output": "空指针"},
                required_items=["空指针"], split=split, state="confirmed",
                source_hash=f"split-{index}", created_by=self.user,
            )
        GoldDatasetVersion.objects.filter(pk=self.version.pk).update(
            state="frozen", content_hash="f" * 64, sample_stats={"case_count": 6},
        )
        self.version.refresh_from_db()
        experiment = OptimizationExperimentService.run_full_race(
            proposal=self.proposal, gold_version=self.version,
            baseline_executor=lambda _input: {"output": "空指针"},
            candidate_executor=lambda _input: {"output": "空指针"},
            candidate_release=self.candidate_release, actor=self.user,
        )
        self.assertEqual(experiment.status, "passed")
        self.assertEqual(set(experiment.gate_report["partition_gates"]), {
            "gold", "regression", "fresh", "challenge", "hidden",
        })
        self.assertTrue(experiment.gate_report["hard_gates"]["all_five_partitions_passed"])
