import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_evolution", "0031_workflowstagegate_stage_choices_order"),
        ("projects", "0004_remove_project_password_remove_project_system_url_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FlywheelRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("workflow_id", models.CharField(db_index=True, max_length=128)),
                ("entry_type", models.CharField(choices=[("requirement", "需求管理"), ("chat", "Agent 对话"), ("test_management", "测试管理"), ("flywheel", "质量飞轮"), ("history_replay", "历史回放")], max_length=24)),
                ("intent", models.CharField(choices=[("production", "生产流程"), ("history_replay", "历史回放"), ("shadow_evaluation", "影子评测")], default="production", max_length=24)),
                ("requirement_document_ids", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("draft", "草稿"), ("running", "运行中"), ("completed", "已完成"), ("failed", "失败"), ("cancelled", "已取消")], db_index=True, default="draft", max_length=16)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="flywheel_runs", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="flywheel_runs", to="projects.project")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.AddConstraint(
            model_name="flywheelrun",
            constraint=models.UniqueConstraint(fields=("project", "workflow_id"), name="uniq_flywheel_run_project_workflow"),
        ),
        migrations.AddIndex(
            model_name="flywheelrun",
            index=models.Index(fields=["project", "status", "updated_at"], name="ke_run_proj_status_idx"),
        ),
    ]
