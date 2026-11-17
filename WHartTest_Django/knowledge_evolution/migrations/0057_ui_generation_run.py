from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0056_ui_generation_context')]
    operations = [migrations.AddField(model_name='skillagenttaskbinding', name='generation_run', field=models.JSONField(default=dict, db_default={}))]
