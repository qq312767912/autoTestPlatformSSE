import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0049_dual_skill_evaluation'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='SkillAssessmentSession', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('source_sha256', models.CharField(max_length=64)), ('mode', models.CharField(max_length=16)),
        ('cases', models.JSONField(default=list)), ('policy', models.JSONField(default=dict)),
        ('rubric', models.JSONField(default=list)), ('baseline', models.JSONField(default=dict)),
        ('candidates', models.JSONField(default=list)), ('winner_id', models.CharField(blank=True, max_length=36)),
        ('state', models.CharField(default='ready', max_length=24)), ('error', models.TextField(blank=True)),
        ('job_id', models.CharField(blank=True, max_length=36)),
        ('budget', models.JSONField(default=dict)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
        ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.project')),
        ('source_version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='skills.skillversion')),
        ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
    ], options={'ordering': ['-created_at']})]
