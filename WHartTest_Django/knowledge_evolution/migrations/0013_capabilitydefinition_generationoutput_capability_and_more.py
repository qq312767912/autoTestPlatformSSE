# Generated manually for CapabilityDefinition and GenerationOutput.capability

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_evolution", "0012_feedbackevent_knowledge_versions_m2m"),
    ]

    operations = [
        migrations.CreateModel(
            name="CapabilityDefinition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("knowledge", "知识"), ("retrieval_policy", "检索策略"), ("prompt", "Prompt"), ("skill", "Skill"), ("agent", "Agent")], db_index=True, max_length=32)),
                ("name", models.CharField(db_index=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("evaluation_mode", models.CharField(choices=[("single", "单次测评"), ("workflow", "链路自进化")], db_index=True, default="single", max_length=20)),
                ("stages", models.JSONField(blank=True, default=list, help_text="有序阶段列表，如 ['risk_identification', 'test_plan_generation', ...]")),
                ("gate_rules", models.JSONField(blank=True, default=dict, help_text="晋级门禁规则：min_mean_diff、max_latency_regression、max_token_regression 等")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("active_release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="active_for_definitions", to="knowledge_evolution.capabilityrelease")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_capability_definitions", to=settings.AUTH_USER_MODEL)),
                ("default_suite", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="capability_definitions", to="knowledge_evolution.evaluationsuite")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="capability_definitions", to="projects.project")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="generationoutput",
            name="capability",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="generation_outputs", to="knowledge_evolution.capabilitydefinition"),
        ),
        migrations.AddConstraint(
            model_name="capabilitydefinition",
            constraint=models.UniqueConstraint(fields=("project", "name"), name="uniq_capability_definition_project_name"),
        ),
        migrations.AddIndex(
            model_name="capabilitydefinition",
            index=models.Index(fields=["project", "kind", "evaluation_mode"], name="knowledge_e_project_5869f1_idx"),
        ),
        migrations.AddIndex(
            model_name="capabilitydefinition",
            index=models.Index(fields=["project", "is_active"], name="knowledge_e_project_051255_idx"),
        ),
    ]
