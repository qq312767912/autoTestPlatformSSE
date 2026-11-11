import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('requirements', '0014_requirementdocument_management_mode'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('projects', '0001_initial'),
    ]
    operations = [migrations.CreateModel(
        name='RequirementComparison',
        fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('old_file', models.FileField(upload_to='requirement_comparisons/%Y/%m/')),
            ('new_file', models.FileField(upload_to='requirement_comparisons/%Y/%m/')),
            ('old_name', models.CharField(max_length=255)),
            ('new_name', models.CharField(max_length=255)),
            ('result', models.JSONField(default=dict)),
            ('confirmed_hunks', models.JSONField(default=list)),
            ('iteration_title', models.CharField(blank=True, max_length=200)),
            ('confirmed_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('creator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.project')),
        ],
    )]
