import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0009_evaluationrun_evaluationresult_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="graphnode", name="node_type",
            field=models.CharField(choices=[
                ("document", "文档"), ("section", "章节"), ("chunk", "分块"),
                ("concept", "概念"), ("rule", "规则/约束"), ("fact", "事实"),
                ("procedure", "流程/步骤"), ("evidence", "证据"), ("asset", "知识资产"),
                ("version", "知识版本"), ("candidate", "知识候选"), ("source", "来源快照"),
                ("workflow_output", "流程产出"),
            ], db_index=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="graphedge", name="relation",
            field=models.CharField(choices=[
                ("CONTAINS", "包含"), ("PART_OF", "属于"), ("NEXT", "下一项"),
                ("MENTIONS", "提及"), ("DEFINES", "定义"), ("REFINES", "细化"),
                ("CONTRADICTS", "矛盾"), ("SUPERSEDES", "取代"),
                ("SUPPORTED_BY", "由…支持"), ("DERIVED_FROM", "派生自"),
                ("ACCEPTED_BY", "被…采纳"), ("REFUTED_BY", "被…反驳"),
                ("FEEDS_INTO", "流转到"),
            ], db_index=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="evaluationsuite", name="task_type",
            field=models.CharField(choices=[("knowledge_query", "知识库问答"), ("case_review", "用例审查"), ("code_review", "代码审查"), ("risk_identification", "风险识别"), ("test_plan_generation", "测试方案生成"), ("test_execution", "测试执行"), ("testcase_generation", "用例生成"), ("issue_tracking", "问题跟踪")], db_index=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="evaluationcase", name="task_type",
            field=models.CharField(choices=[("knowledge_query", "知识库问答"), ("case_review", "用例审查"), ("code_review", "代码审查"), ("risk_identification", "风险识别"), ("test_plan_generation", "测试方案生成"), ("test_execution", "测试执行"), ("testcase_generation", "用例生成"), ("issue_tracking", "问题跟踪")], max_length=32),
        ),
        migrations.CreateModel(
            name="CapabilityRelease",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("knowledge", "知识"), ("retrieval_policy", "检索策略"), ("prompt", "Prompt"), ("skill", "Skill"), ("agent", "Agent")], db_index=True, max_length=32)),
                ("name", models.CharField(max_length=255)),
                ("version", models.CharField(max_length=100)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("artifact_hash", models.CharField(db_index=True, max_length=64)),
                ("state", models.CharField(choices=[("draft", "草稿"), ("shadow", "影子验证"), ("awaiting_approval", "待审批"), ("active", "生产生效"), ("retired", "已退役"), ("rolled_back", "已回滚"), ("rejected", "未通过")], db_index=True, default="draft", max_length=24)),
                ("gate_report", models.JSONField(blank=True, default=dict)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_capability_releases", to=settings.AUTH_USER_MODEL)),
                ("baseline_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="baseline_releases", to="knowledge_evolution.evaluationrun")),
                ("candidate", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="capability_releases", to="knowledge_evolution.knowledgecandidate")),
                ("candidate_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="candidate_releases", to="knowledge_evolution.evaluationrun")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_capability_releases", to=settings.AUTH_USER_MODEL)),
                ("previous_release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="successors", to="knowledge_evolution.capabilityrelease")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="capability_releases", to="projects.project")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PromotionDecision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("decision", models.CharField(choices=[("approved", "通过"), ("rejected", "驳回"), ("rollback", "回滚")], max_length=16)),
                ("gate_snapshot", models.JSONField(blank=True, default=dict)),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="capability_decisions", to=settings.AUTH_USER_MODEL)),
                ("release", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="knowledge_evolution.capabilityrelease")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="capabilityrelease", index=models.Index(fields=["project", "kind", "state"], name="knowledge_e_project_2e9084_idx")),
        migrations.AddConstraint(model_name="capabilityrelease", constraint=models.UniqueConstraint(fields=("project", "kind", "version"), name="uniq_capability_release_version")),
    ]
