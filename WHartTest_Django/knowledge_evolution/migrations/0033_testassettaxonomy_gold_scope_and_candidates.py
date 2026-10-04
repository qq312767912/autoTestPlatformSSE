import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_evolution", "0032_flywheelrun"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TestAssetTaxonomy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scope_key", models.SlugField(db_index=True, max_length=64)),
                ("version", models.CharField(max_length=64)),
                ("state", models.CharField(choices=[("draft", "草稿"), ("review", "待审批"), ("published", "已发布"), ("retired", "已退役")], db_index=True, default="draft", max_length=16)),
                ("categories", models.JSONField(blank=True, default=list)),
                ("critical_scenarios", models.JSONField(blank=True, default=list)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("content_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_test_asset_taxonomies", to=settings.AUTH_USER_MODEL)),
                ("maintained_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="maintained_test_asset_taxonomies", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="test_asset_taxonomies", to="projects.project")),
            ],
            options={"ordering": ["scope_key", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="testassettaxonomy",
            constraint=models.UniqueConstraint(fields=("project", "scope_key", "version"), name="uniq_taxonomy_project_scope_version"),
        ),
        migrations.AddField(model_name="golddataset", name="scope_type", field=models.CharField(choices=[("general", "通用"), ("domain", "业务专用")], default="general", max_length=16)),
        migrations.AddField(model_name="golddataset", name="scope_key", field=models.SlugField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="golddataset", name="governance", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="golddataset", name="approver", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_gold_datasets", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="golddataset", name="taxonomy_version", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="gold_datasets", to="knowledge_evolution.testassettaxonomy")),
        migrations.RemoveConstraint(model_name="golddataset", name="uniq_gold_dataset_project_name_task"),
        migrations.AddConstraint(
            model_name="golddataset",
            constraint=models.UniqueConstraint(fields=("project", "name", "task_type", "scope_key"), name="uniq_gold_dataset_project_name_task_scope"),
        ),
        migrations.AddField(model_name="goldcase", name="candidate_origin", field=models.CharField(choices=[("history", "历史资料"), ("feedback", "人工反馈"), ("defect", "确认缺陷"), ("missed", "漏测"), ("false_positive", "误报"), ("rollback", "回滚"), ("manual", "人工创建")], db_index=True, default="manual", max_length=24)),
        migrations.AddField(model_name="goldcase", name="candidate_score", field=models.FloatField(default=0.0)),
        migrations.AddField(model_name="goldcase", name="recommended_split", field=models.CharField(blank=True, choices=[("gold", "Gold"), ("regression", "Regression"), ("fresh", "Fresh"), ("challenge", "Challenge"), ("hidden", "Hidden")], max_length=20)),
        migrations.AddField(model_name="goldcase", name="recommended_tags", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="goldcase", name="dedup_fingerprint", field=models.CharField(blank=True, db_index=True, max_length=64)),
        migrations.AddField(model_name="goldcase", name="review_checklist", field=models.JSONField(blank=True, default=dict)),
    ]
