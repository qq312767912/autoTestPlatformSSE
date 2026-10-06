from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('skills', '0005_skill_declared_stage'),
    ]

    operations = [
        migrations.CreateModel(
            name='SkillWorkshopExclusion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('removed_at', models.DateTimeField(auto_now_add=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='skill_workshop_exclusions', to='projects.project')),
                ('removed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('source_skill', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workshop_exclusions', to='skills.skill')),
            ],
            options={'unique_together': {('project', 'source_skill')}},
        ),
    ]
