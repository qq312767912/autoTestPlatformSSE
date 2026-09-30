import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0017_executionspan_failureattribution_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OptimizationProposal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("proposal_type", models.CharField(choices=[("prompt", "Prompt"), ("knowledge", "知识"), ("retrieval_policy", "检索策略"), ("skill_tool", "Skill/工具配置")], db_index=True, max_length=32)),
                ("title", models.CharField(max_length=255)),
                ("summary", models.TextField()),
                ("change_patch", models.JSONField(default=dict)),
                ("expected_benefit", models.JSONField(blank=True, default=dict)),
                ("impact_scope", models.JSONField(blank=True, default=dict)),
                ("risk_notes", models.JSONField(blank=True, default=list)),
                ("rollback_plan", models.JSONField(blank=True, default=dict)),
                ("state", models.CharField(choices=[("draft", "草稿"), ("evaluating", "评测中"), ("awaiting_approval", "待审批"), ("approved", "已批准"), ("rejected", "已驳回"), ("superseded", "已取代")], db_index=True, default="draft", max_length=24)),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("generated_by", models.CharField(default="controlled-optimizer-v1", max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("attributions", models.ManyToManyField(related_name="optimization_proposals", to="knowledge_evolution.failureattribution")),
                ("baseline_release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="optimization_proposals", to="knowledge_evolution.capabilityrelease")),
                ("capability", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="optimization_proposals", to="knowledge_evolution.capabilitydefinition")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_optimization_proposals", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="optimization_proposals", to="projects.project")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="OptimizationExperiment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("pending", "待运行"), ("running", "运行中"), ("passed", "通过"), ("failed", "失败"), ("cancelled", "取消")], db_index=True, default="pending", max_length=16)),
                ("gate_report", models.JSONField(blank=True, default=dict)),
                ("shadow_metrics", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("baseline_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="optimization_baseline_experiments", to="knowledge_evolution.evaluationrun")),
                ("candidate_release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="optimization_experiments", to="knowledge_evolution.capabilityrelease")),
                ("candidate_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="optimization_candidate_experiments", to="knowledge_evolution.evaluationrun")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_optimization_experiments", to=settings.AUTH_USER_MODEL)),
                ("gold_dataset_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="optimization_experiments", to="knowledge_evolution.golddatasetversion")),
                ("proposal", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="experiments", to="knowledge_evolution.optimizationproposal")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="optimizationproposal", index=models.Index(fields=["project", "proposal_type", "state"], name="knowledge_e_project_d43dae_idx")),
        migrations.AddIndex(model_name="optimizationexperiment", index=models.Index(fields=["proposal", "status"], name="knowledge_e_proposa_789d83_idx")),
    ]
