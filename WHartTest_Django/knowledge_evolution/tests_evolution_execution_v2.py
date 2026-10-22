import hashlib
import tempfile

from django.core.exceptions import ValidationError
from django.test import override_settings

from knowledge_evolution.evolution_execution import (
    ExecutablePatchBuilder, EvolutionExecutionService,
)
from knowledge_evolution.optimization_models import EvolutionExperiment, EvolutionVariant
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.trace_models import FailureAttribution


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="evolution-v2-media-")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ExecutablePatchV2Tests(SkillHubBaseTests):
    def attribution(self, output, *, target="few_shot", suffix="one"):
        primary_cause = "tools_logic" if target == "tool_schema" else "prompt_rules"
        fine_cause = "invalid_tool_argument" if target == "tool_schema" else "boundary_uncovered"
        return FailureAttribution.objects.create(
            project=self.project, output=output, category="prompt_error",
            primary_cause=primary_cause, fine_cause=fine_cause,
            repair_target=target, evidence_status="verified",
            diagnosis_version="skill_attribution/v2", state="confirmed",
            hypothesis=f"补齐边界约束 {suffix}",
            evidence=[{"evidence_id": f"report:{suffix}", "locatable": True}],
            fingerprint=hashlib.sha256(suffix.encode()).hexdigest(),
        )

    def test_patch_routes_few_shot_and_tool_schema_to_separate_assets(self):
        _skill, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        items = [
            self.attribution(output, target="few_shot", suffix="few"),
            self.attribution(output, target="tool_schema", suffix="tool"),
        ]

        patch = ExecutablePatchBuilder.build(skill_version=version, attributions=items)

        self.assertEqual(patch["schema"], "skill_patch/v2")
        paths = {item["path"] for item in patch["edits"]}
        self.assertIn("SKILL.md", paths)
        self.assertIn("references/evolution-few-shot.md", paths)
        self.assertIn("schemas/evolution-tool-constraints.json", paths)
        self.assertEqual(patch["impact"]["risk_level"], "medium")

    def test_unverified_attribution_cannot_generate_patch(self):
        _skill, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        item = self.attribution(output, suffix="unverified")
        item.evidence_status = "unverified"
        item.save(update_fields=["evidence_status"])

        with self.assertRaisesMessage(ValidationError, "证据已验证"):
            ExecutablePatchBuilder.build(skill_version=version, attributions=[item])

    def test_winner_requires_three_completed_runs_and_reason(self):
        _skill, version = self.make_skill_version()
        output = self.make_output(skill_version=version)
        experiment = EvolutionExperiment.objects.create(
            project=self.project, output=output, baseline_skill_version=version,
            baseline_sha256=version.package_sha256, created_by=self.lead,
        )
        for label in ("baseline", "A", "B"):
            EvolutionVariant.objects.create(
                experiment=experiment, label=label,
                mutation_strategy="unchanged" if label == "baseline" else "test",
                skill_version=version, sandbox_key=f"test:{experiment.id}:{label}",
                rollback_target=str(version.id),
                fingerprint=hashlib.sha256(f"{experiment.id}:{label}".encode()).hexdigest(),
            )
        with self.assertRaisesMessage(ValidationError, "尚未全部重跑"):
            EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="更好")

        experiment.variants.update(status="completed")
        experiment.state = "awaiting_choice"
        experiment.save(update_fields=["state"])
        with self.assertRaisesMessage(ValidationError, "对比理由"):
            EvolutionExecutionService.choose_winner(experiment=experiment, label="A", reason="")

        EvolutionExecutionService.choose_winner(experiment=experiment, label="B", reason="结构更完整")
        experiment.refresh_from_db()
        self.assertEqual(experiment.winner_label, "B")
        self.assertEqual(experiment.state, "winner_selected")
