from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("code_analysis", "0005_analysistask_context_document_ids"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalysisTaskExecutionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.CharField(choices=[("created", "已创建"), ("queued", "已入队"), ("started", "已开始"), ("completed", "已完成"), ("partial", "部分完成"), ("failed", "执行失败"), ("cancelled", "已取消")], max_length=20)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="code_analysis_execution_logs", to=settings.AUTH_USER_MODEL)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="execution_logs", to="code_analysis.analysistask")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
