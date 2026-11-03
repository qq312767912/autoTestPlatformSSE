from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0048_evolution_paired_evaluation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name="evolutionexperiment", name="assessment_config", field=models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(
            name="SkillEvaluationProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("metrics", models.JSONField(blank=True, default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="projects.project")),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="skills.skill")),
                ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("project", "skill"), name="uniq_skill_evaluation_profile")]},
        ),
    ]
