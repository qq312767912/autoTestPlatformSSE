from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0054_skillagenttaskbinding_generation_mode')]
    operations = [migrations.AlterField(model_name='skillagenttaskbinding', name='generation_mode', field=models.CharField(max_length=32, blank=True, default='', db_default=''))]
