from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0055_generation_mode_database_default')]
    operations = [migrations.AddField(model_name='skillagenttaskbinding', name='generation_context', field=models.JSONField(default=dict, db_default={}))]
