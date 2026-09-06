from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0004_analysistask_analysis_context")]
    operations = [migrations.AddField(model_name="analysistask", name="requirement_document_id", field=models.UUIDField(blank=True, null=True)), migrations.AddField(model_name="analysistask", name="api_document_id", field=models.UUIDField(blank=True, null=True))]
