from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0053_alter_evolutionexperiment_baseline_skill_version_and_more')]
    operations = [migrations.AddField(model_name='skillagenttaskbinding', name='generation_mode', field=models.CharField(max_length=32, blank=True, default=''))]
