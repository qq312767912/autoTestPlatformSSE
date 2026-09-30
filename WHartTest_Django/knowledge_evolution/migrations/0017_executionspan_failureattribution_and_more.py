import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0016_evaluationrun_capability_release_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExecutionSpan",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("workflow_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("stage", models.CharField(db_index=True, max_length=64)),
                ("step_type", models.CharField(choices=[("intent", "意图理解"), ("planning", "任务规划"), ("retrieval", "知识检索"), ("graph_retrieval", "图谱检索"), ("prompt", "Prompt组装"), ("model", "模型推理"), ("tool", "工具调用"), ("validation", "结果校验"), ("handoff", "Agent交接"), ("human_edit", "人工修改"), ("downstream", "下游执行")], db_index=True, max_length=32)),
                ("sequence", models.PositiveIntegerField(default=0)),
                ("agent_name", models.CharField(blank=True, max_length=128)),
                ("agent_version", models.CharField(blank=True, max_length=128)),
                ("prompt_version", models.CharField(blank=True, max_length=128)),
                ("model_version", models.CharField(blank=True, max_length=200)),
                ("knowledge_version_ids", models.JSONField(blank=True, default=list)),
                ("tool_name", models.CharField(blank=True, max_length=128)),
                ("tool_version", models.CharField(blank=True, max_length=128)),
                ("input_hash", models.CharField(blank=True, max_length=64)),
                ("output_hash", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("running", "运行中"), ("completed", "完成"), ("failed", "失败"), ("skipped", "跳过")], db_index=True, default="running", max_length=20)),
                ("error_type", models.CharField(blank=True, db_index=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("token_usage", models.PositiveIntegerField(default=0)),
                ("evidence", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("parent_span", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="knowledge_evolution.executionspan")),
                ("trace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="spans", to="knowledge_evolution.retrievaltrace")),
            ],
            options={"ordering": ["trace", "sequence", "created_at"]},
        ),
        migrations.CreateModel(
            name="FailureAttribution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("workflow_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("category", models.CharField(choices=[("intent_error", "理解失败"), ("planning_error", "规划失败"), ("knowledge_missing", "知识缺失"), ("knowledge_stale", "知识过期"), ("retrieval_error", "检索失败"), ("prompt_error", "Prompt失败"), ("tool_error", "工具失败"), ("generation_error", "生成失败"), ("downstream_execution_error", "下游执行失败")], db_index=True, max_length=40)),
                ("source", models.CharField(choices=[("rule", "确定性规则"), ("llm", "LLM辅助"), ("human", "人工")], default="rule", max_length=16)),
                ("confidence", models.FloatField(default=1.0)),
                ("hypothesis", models.TextField()),
                ("evidence", models.JSONField(blank=True, default=list)),
                ("counterevidence", models.JSONField(blank=True, default=list)),
                ("state", models.CharField(choices=[("proposed", "待确认"), ("confirmed", "已确认"), ("rejected", "已驳回")], db_index=True, default="proposed", max_length=16)),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_failure_attributions", to=settings.AUTH_USER_MODEL)),
                ("output", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="failure_attributions", to="knowledge_evolution.generationoutput")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="failure_attributions", to="projects.project")),
                ("span", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="failure_attributions", to="knowledge_evolution.executionspan")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="executionspan", index=models.Index(fields=["trace", "stage", "sequence"], name="knowledge_e_trace_i_f39726_idx")),
        migrations.AddIndex(model_name="executionspan", index=models.Index(fields=["workflow_id", "status"], name="knowledge_e_workflo_d84a4e_idx")),
        migrations.AddIndex(model_name="failureattribution", index=models.Index(fields=["project", "category", "state"], name="knowledge_e_project_e04be6_idx")),
        migrations.AddIndex(model_name="failureattribution", index=models.Index(fields=["workflow_id", "state"], name="knowledge_e_workflo_2f208c_idx")),
    ]
