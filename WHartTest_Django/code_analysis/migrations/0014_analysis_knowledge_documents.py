from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0013_alter_projectrepository_default_branch")]
    operations = [
        migrations.AddField(model_name="analysistask", name="knowledge_base_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="analysistask", name="knowledge_document_ids", field=models.JSONField(blank=True, default=list)),
    ]
