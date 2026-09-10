import testcases.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0001_initial"),
        ("testcases", "0022_alter_testcasestep_expected_result"),
    ]

    operations = [
        migrations.CreateModel(
            name="TestCaseReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_file", models.FileField(max_length=500, upload_to=testcases.models.testcase_review_source_path)),
                ("source_name", models.CharField(max_length=255)),
                ("business_context", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("pending", "等待中"), ("running", "审查中"), ("completed", "已完成"), ("failed", "失败")], default="pending", max_length=20)),
                ("current_step", models.CharField(blank=True, default="等待执行", max_length=255)),
                ("progress", models.PositiveSmallIntegerField(default=0)),
                ("report_file", models.FileField(blank=True, max_length=500, null=True, upload_to=testcases.models.testcase_review_report_path)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("celery_task_id", models.CharField(blank=True, default="", max_length=255)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("creator", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_testcase_reviews", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="testcase_reviews", to="projects.project")),
            ],
            options={"verbose_name": "用例审查", "verbose_name_plural": "用例审查", "ordering": ["-created_at"]},
        )
    ]
