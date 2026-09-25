from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("testcases", "0026_merge_20260922_1918")]
    operations = [
        migrations.AddField(model_name="testcasereview", name="requirement_document_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="testcasereview", name="knowledge_base_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="testcasereview", name="knowledge_document_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AlterField(
            model_name="testcasereview", name="status",
            field=models.CharField(choices=[("pending", "等待中"), ("running", "审查中"), ("completed", "已完成"), ("failed", "失败"), ("cancelled", "已取消")], default="pending", max_length=20),
        ),
    ]
