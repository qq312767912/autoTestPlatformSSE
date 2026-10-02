"""冻结金标回放与受控候选实验。"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from .capabilities import CapabilityReleaseService
from .evaluation import EvaluationEngine
from .gold_evaluation import GoldEvaluationBridge
from .optimization_models import OptimizationExperiment


class RetrievalPolicyReplayExecutor:
    """把版本化 RetrievalPolicy 绑定到金标回放执行器。"""

    def __init__(self, *, policy, answer_generator, orchestrator=None):
        from .retrieval import RetrievalOrchestrator
        self.policy = policy
        self.answer_generator = answer_generator
        self.orchestrator = orchestrator or RetrievalOrchestrator()

    def __call__(self, input_payload):
        from .retrieval import RetrievalRequest
        query = str(input_payload.get("query") or input_payload.get("input") or "")
        request = RetrievalRequest(
            project_id=self.policy.project_id, query=query,
            task_type=str(input_payload.get("task_type") or "knowledge_query"),
            policy=self.policy,
            top_k=int(input_payload.get("top_k") or 10),
        )
        retrieval = self.orchestrator.retrieve(request)
        evidence = retrieval.get("evidence_package") or []
        generated = self.answer_generator(input_payload, evidence)
        if not isinstance(generated, dict):
            generated = {"output": str(generated)}
        return {
            **generated,
            "citations": [item.get("citation_id") or item.get("id") for item in evidence],
            "retrieval_policy_id": str(self.policy.id),
            "retrieval_latency_ms": retrieval.get("latency_ms", 0),
            "retrieval_token_estimate": retrieval.get("token_estimate", 0),
        }


class GoldReplayService:
    """将一个冻结金标版本回放为可追溯 EvaluationRun。"""

    @staticmethod
    def run(*, version, executor, actor=None, name="", config=None, capability_release=None):
        if version.state != "frozen" or not version.content_hash:
            raise ValidationError("只能回放已冻结的金标版本")
        suite = GoldEvaluationBridge.materialize(version=version, actor=actor)
        engine = EvaluationEngine(suite=suite, executor=executor, config=config or {})
        run = engine.start_run(name=name, triggered_by=actor)
        run.gold_dataset_version = version
        run.capability_release = capability_release
        run.replay_hash = version.content_hash
        run.save(update_fields=["gold_dataset_version", "capability_release", "replay_hash", "updated_at"])
        return engine.execute(run)


class OptimizationExperimentService:
    """基线与候选使用同一冻结快照赛马，并将完整门禁持久化。"""

    @staticmethod
    @transaction.atomic
    def run(*, proposal, gold_version, baseline_executor, candidate_executor,
            candidate_release, actor=None, gate_rules=None):
        if proposal.state not in {"draft", "evaluating"}:
            raise ValidationError("仅草稿或评测中的候选可以启动实验")
        if candidate_release.project_id != proposal.project_id:
            raise ValidationError("候选发布与优化候选必须属于同一项目")
        experiment = OptimizationExperiment.objects.create(
            proposal=proposal, gold_dataset_version=gold_version,
            candidate_release=candidate_release, status="running", created_by=actor,
        )
        proposal.state = "evaluating"
        proposal.save(update_fields=["state", "updated_at"])
        try:
            baseline = GoldReplayService.run(
                version=gold_version, executor=baseline_executor, actor=actor,
                name=f"{proposal.title}-baseline", config={"role": "baseline"},
                capability_release=proposal.baseline_release,
            )
            candidate = GoldReplayService.run(
                version=gold_version, executor=candidate_executor, actor=actor,
                name=f"{proposal.title}-candidate", config={"role": "candidate"},
                capability_release=candidate_release,
            )
            report = CapabilityReleaseService.evaluate_shadow(
                candidate_release, baseline, candidate, **(gate_rules or {}),
            )
            experiment.baseline_run = baseline
            experiment.candidate_run = candidate
            experiment.gate_report = report
            experiment.status = "passed" if report["passed"] else "failed"
            proposal.state = "awaiting_approval" if report["passed"] else "draft"
            proposal.save(update_fields=["state", "updated_at"])
            experiment.save(update_fields=[
                "baseline_run", "candidate_run", "gate_report", "status", "updated_at",
            ])
            return experiment
        except Exception:
            experiment.status = "failed"
            experiment.save(update_fields=["status", "updated_at"])
            proposal.state = "draft"
            proposal.save(update_fields=["state", "updated_at"])
            raise

    @staticmethod
    def run_retrieval_policy(*, proposal, gold_version, baseline_policy, candidate_policy,
                             answer_generator, candidate_release, actor=None,
                             gate_rules=None, orchestrator=None):
        if proposal.proposal_type != "retrieval_policy":
            raise ValidationError("只有检索策略候选可使用策略回放")
        if baseline_policy.project_id != proposal.project_id or candidate_policy.project_id != proposal.project_id:
            raise ValidationError("检索策略与优化候选必须属于同一项目")
        return OptimizationExperimentService.run(
            proposal=proposal, gold_version=gold_version,
            baseline_executor=RetrievalPolicyReplayExecutor(
                policy=baseline_policy, answer_generator=answer_generator, orchestrator=orchestrator,
            ),
            candidate_executor=RetrievalPolicyReplayExecutor(
                policy=candidate_policy, answer_generator=answer_generator, orchestrator=orchestrator,
            ),
            candidate_release=candidate_release, actor=actor, gate_rules=gate_rules,
        )

    @staticmethod
    @transaction.atomic
    def run_full_race(*, proposal, gold_version, baseline_executor, candidate_executor,
                      candidate_release, actor=None, gate_rules=None):
        required_splits = {"gold", "regression", "fresh", "challenge", "hidden"}
        available = set(gold_version.cases.values_list("split", flat=True))
        missing = sorted(required_splits - available)
        if missing:
            raise ValidationError(f"完整候选赛马缺少分区: {missing}")
        experiment = OptimizationExperimentService.run(
            proposal=proposal, gold_version=gold_version,
            baseline_executor=baseline_executor, candidate_executor=candidate_executor,
            candidate_release=candidate_release, actor=actor, gate_rules=gate_rules,
        )
        partition_report = {}
        partitions_passed = True
        for split in sorted(required_splits):
            comparisons = {
                level: EvaluationEngine.compare_runs(
                    experiment.baseline_run.results.filter(status="completed", case__split=split),
                    experiment.candidate_run.results.filter(status="completed", case__split=split),
                    level,
                ) for level in ("l0", "l1", "l2", "l3")
            }
            split_passed = all(
                item.get("n", 0) > 0 and (item.get("mean_diff") or 0) >= 0
                for item in comparisons.values()
            )
            partition_report[split] = {"passed": split_passed, "comparisons": comparisons}
            partitions_passed = partitions_passed and split_passed
        report = dict(experiment.gate_report or {})
        report["partition_gates"] = partition_report
        report["passed"] = bool(report.get("passed")) and partitions_passed
        report.setdefault("hard_gates", {})["all_five_partitions_passed"] = partitions_passed
        experiment.gate_report = report
        experiment.status = "passed" if report["passed"] else "failed"
        experiment.save(update_fields=["gate_report", "status", "updated_at"])
        candidate_release.gate_report = report
        candidate_release.state = "awaiting_approval" if report["passed"] else "rejected"
        candidate_release.save(update_fields=["gate_report", "state", "updated_at"])
        proposal.state = "awaiting_approval" if report["passed"] else "draft"
        proposal.save(update_fields=["state", "updated_at"])
        return experiment
