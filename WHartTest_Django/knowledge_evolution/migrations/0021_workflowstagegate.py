import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0020_releaseobservation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkflowStageGate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("workflow_id", models.CharField(db_index=True, max_length=128)),
                ("stage", models.CharField(choices=[("risk_identification", "风险识别"), ("test_plan_generation", "测试方案生成"), ("testcase_generation", "测试用例生成"), ("test_execution", "测试执行"), ("issue_tracking", "问题跟踪")], db_index=True, max_length=32)),
                ("status", models.CharField(choices=[("pending", "待测评"), ("passed", "测评通过"), ("failed", "测评失败"), ("overridden", "负责人放行")], db_index=True, default="pending", max_length=20)),
                ("scores", models.JSONField(blank=True, default=dict)),
                ("threshold", models.FloatField(default=0.7)),
                ("reason", models.TextField(blank=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="workflow_gate_decisions", to=settings.AUTH_USER_MODEL)),
                ("output", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="workflow_gates", to="knowledge_evolution.generationoutput")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workflow_stage_gates", to="projects.project")),
            ],
            options={"ordering": ["workflow_id", "created_at"]},
        ),
        migrations.AddConstraint(
            model_name="workflowstagegate",
            constraint=models.UniqueConstraint(fields=("project", "workflow_id", "stage"), name="uniq_workflow_stage_gate"),
        ),
        migrations.AddIndex(
            model_name="workflowstagegate",
            index=models.Index(fields=["project", "workflow_id", "status"], name="ke_gate_proj_wf_status_idx"),
        ),
    ]
