from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("knowledge_evolution", "0047_benchmark_catalog_models")]
    operations = [
        migrations.AddField(model_name="evolutionexperiment", name="policy", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="evolutionexperiment", name="evaluation_report", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="evolutionexperiment", name="round_number", field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name="evolutionexperiment", name="parent", field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="next_round", to="knowledge_evolution.evolutionexperiment")),
    ]
