import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .capability_models import CapabilityRelease, PromotionDecision
from .evaluation import EvaluationEngine


class CapabilityReleaseService:
    @staticmethod
    def create(*, project, kind, name, version, config, actor=None, candidate=None):
        canonical = json.dumps(config or {}, ensure_ascii=False, sort_keys=True)
        return CapabilityRelease.objects.create(
            project=project, kind=kind, name=name, version=version, config=config or {},
            artifact_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            candidate=candidate, created_by=actor,
        )

    @staticmethod
    def evaluate_shadow(release, baseline_run, candidate_run, *, min_mean_diff=0.0,
                        max_latency_regression=0.2, max_token_regression=0.2):
        if baseline_run.suite_id != candidate_run.suite_id:
            raise ValidationError("基线与候选必须使用同一评测集")
        comparisons = {
            level: EvaluationEngine.compare_runs(
                baseline_run.results.filter(status="completed"),
                candidate_run.results.filter(status="completed"), level,
            ) for level in ("l0", "l1", "l2", "l3")
        }
        baseline_cost, candidate_cost = baseline_run.cost_summary or {}, candidate_run.cost_summary or {}
        def regression(key):
            base = float(baseline_cost.get(key) or 0)
            candidate = float(candidate_cost.get(key) or 0)
            return 0.0 if base <= 0 else (candidate - base) / base
        hard_gates = {
            "same_sample_count": baseline_run.results.count() == candidate_run.results.count(),
            "no_l1_regression": (comparisons["l1"]["mean_diff"] or 0) >= min_mean_diff,
            "no_l2_regression": (comparisons["l2"]["mean_diff"] or 0) >= min_mean_diff,
            "latency_within_budget": regression("total_latency_ms") <= max_latency_regression,
            "tokens_within_budget": regression("total_tokens") <= max_token_regression,
        }
        passed = all(hard_gates.values())
        release.baseline_run = baseline_run
        release.candidate_run = candidate_run
        release.gate_report = {"passed": passed, "hard_gates": hard_gates, "comparisons": comparisons}
        release.state = "awaiting_approval" if passed else "rejected"
        release.save(update_fields=["baseline_run", "candidate_run", "gate_report", "state", "updated_at"])
        return release.gate_report

    @staticmethod
    @transaction.atomic
    def promote(release, *, actor, reason=""):
        if release.state != "awaiting_approval" or not release.gate_report.get("passed"):
            raise ValidationError("候选尚未通过影子门禁")
        current = CapabilityRelease.objects.select_for_update().filter(
            project=release.project, kind=release.kind, state="active"
        ).exclude(pk=release.pk).first()
        if current:
            current.state = "retired"
            current.save(update_fields=["state", "updated_at"])
        release.previous_release = current
        release.state = "active"
        release.approved_by = actor
        release.approved_at = timezone.now()
        release.activated_at = timezone.now()
        release.save()
        PromotionDecision.objects.create(release=release, decision="approved", gate_snapshot=release.gate_report, reason=reason, actor=actor)
        return release

    @staticmethod
    @transaction.atomic
    def rollback(release, *, actor, reason=""):
        if release.state != "active":
            raise ValidationError("只有生产生效版本可以回滚")
        previous = release.previous_release
        release.state = "rolled_back"
        release.save(update_fields=["state", "updated_at"])
        if previous:
            previous.state = "active"
            previous.activated_at = timezone.now()
            previous.save(update_fields=["state", "activated_at", "updated_at"])
        PromotionDecision.objects.create(release=release, decision="rollback", gate_snapshot=release.gate_report, reason=reason, actor=actor)
        return previous
