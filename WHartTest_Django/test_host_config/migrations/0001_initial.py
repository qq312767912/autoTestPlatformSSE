import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="TestHostConfigVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveBigIntegerField(unique=True)),
                ("source_draft_revision", models.PositiveBigIntegerField()),
                ("snapshot", models.JSONField(default=list)),
                ("checksum", models.CharField(db_index=True, max_length=64)),
                ("status", models.CharField(choices=[("publishing", "发布中"), ("published", "已发布"), ("failed", "发布失败"), ("superseded", "已被替代")], default="publishing", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_test_host_versions", to=settings.AUTH_USER_MODEL)),
                ("source_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rollback_versions", to="test_host_config.testhostconfigversion")),
            ],
            options={"db_table": "test_host_config_version", "ordering": ["-version"]},
        ),
        migrations.CreateModel(
            name="TestHostMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("system_name", models.CharField(max_length=100, verbose_name="系统名称")),
                ("hostname", models.CharField(max_length=253, unique=True, verbose_name="域名")),
                ("ipv4", models.GenericIPAddressField(protocol="IPv4", verbose_name="IPv4")),
                ("enabled", models.BooleanField(default=True, verbose_name="是否启用")),
                ("remark", models.CharField(blank=True, default="", max_length=500, verbose_name="备注")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_test_host_mappings", to=settings.AUTH_USER_MODEL, verbose_name="创建人")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_test_host_mappings", to=settings.AUTH_USER_MODEL, verbose_name="更新人")),
            ],
            options={
                "db_table": "test_host_mapping", "ordering": ["system_name", "hostname"],
                "verbose_name": "测试域名映射", "verbose_name_plural": "测试域名映射",
                "permissions": [("publish_testhostconfig", "发布测试域名配置"), ("rollback_testhostconfig", "回滚测试域名配置"), ("diagnose_testhostmapping", "诊断测试域名映射")],
            },
        ),
        migrations.CreateModel(
            name="TestHostNodeStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("node_id", models.CharField(max_length=128, unique=True)),
                ("node_type", models.CharField(max_length=32)),
                ("display_name", models.CharField(max_length=128)),
                ("applied_version", models.PositiveBigIntegerField(blank=True, null=True)),
                ("applied_checksum", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.CharField(choices=[("synced", "已同步"), ("pending", "待同步"), ("failed", "失败")], default="pending", max_length=20)),
                ("message", models.CharField(blank=True, default="", max_length=500)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("last_seen_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "test_host_node_status", "ordering": ["node_type", "node_id"]},
        ),
        migrations.CreateModel(
            name="TestHostConfigState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("draft_revision", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("published_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="active_states", to="test_host_config.testhostconfigversion")),
            ],
            options={"db_table": "test_host_config_state"},
        ),
        migrations.CreateModel(
            name="TestHostDiagnosis",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "等待诊断"), ("running", "诊断中"), ("success", "诊断通过"), ("failed", "诊断失败")], default="pending", max_length=20)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("error_message", models.CharField(blank=True, default="", max_length=500)),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("mapping", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="diagnoses", to="test_host_config.testhostmapping")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="test_host_diagnoses", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "test_host_diagnosis", "ordering": ["-created_at"]},
        ),
    ]
