from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0008_testrequirementdraft_converted_test_case")]

    operations = [
        migrations.AddField(model_name="analysistask", name="requirement_document_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="analysistask", name="api_document_ids", field=models.JSONField(blank=True, default=list)),
    ]
