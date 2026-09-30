import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0015_goldcase_goldannotation_annotationconflict_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluationrun", name="capability_release",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="evaluation_runs_v2", to="knowledge_evolution.capabilityrelease"),
        ),
        migrations.AddField(
            model_name="evaluationrun", name="gold_dataset_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="evaluation_runs", to="knowledge_evolution.golddatasetversion"),
        ),
        migrations.AddField(
            model_name="evaluationrun", name="replay_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.CreateModel(
            name="EvaluationRubric",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("task_type", models.CharField(db_index=True, max_length=32)),
                ("name", models.CharField(max_length=255)),
                ("version", models.CharField(max_length=64)),
                ("dimensions", models.JSONField(blank=True, default=list)),
                ("required_items", models.JSONField(blank=True, default=list)),
                ("forbidden_items", models.JSONField(blank=True, default=list)),
                ("jury_config", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_evaluation_rubrics", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="evaluation_rubrics", to="projects.project")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="JudgeResult",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("level", models.CharField(choices=[("l0", "L0"), ("l1", "L1"), ("l2", "L2"), ("l3", "L3")], db_index=True, max_length=4)),
                ("evaluator_type", models.CharField(db_index=True, max_length=64)),
                ("evaluator_version", models.CharField(max_length=64)),
                ("judge_name", models.CharField(max_length=128)),
                ("model_version", models.CharField(blank=True, max_length=200)),
                ("score", models.FloatField(blank=True, null=True)),
                ("passed", models.BooleanField(blank=True, null=True)),
                ("confidence", models.FloatField(default=1.0)),
                ("dimensions", models.JSONField(blank=True, default=dict)),
                ("evidence", models.JSONField(blank=True, default=list)),
                ("rationale", models.TextField(blank=True)),
                ("raw_output", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("completed", "完成"), ("failed", "失败"), ("needs_review", "待人工复核")], db_index=True, default="completed", max_length=20)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("token_usage", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("evaluation_result", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="judge_results", to="knowledge_evolution.evaluationresult")),
                ("rubric", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="judge_results", to="knowledge_evolution.evaluationrubric")),
            ],
            options={"ordering": ["evaluation_result", "level", "judge_name"]},
        ),
        migrations.AddIndex(model_name="evaluationrubric", index=models.Index(fields=["project", "task_type", "is_active"], name="knowledge_e_project_13fe6b_idx")),
        migrations.AddConstraint(model_name="evaluationrubric", constraint=models.UniqueConstraint(fields=("project", "task_type", "name", "version"), name="uniq_evaluation_rubric_version")),
        migrations.AddIndex(model_name="judgeresult", index=models.Index(fields=["level", "status", "created_at"], name="knowledge_e_level_687512_idx")),
        migrations.AddConstraint(model_name="judgeresult", constraint=models.UniqueConstraint(fields=("evaluation_result", "level", "evaluator_type", "judge_name"), name="uniq_judge_result_per_evaluator")),
    ]
