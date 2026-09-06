from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0003_projectrepository_local_git_source")]

    operations = [
        migrations.AddField(model_name="analysistask", name="requirement_context", field=models.TextField(blank=True)),
        migrations.AddField(model_name="analysistask", name="api_context", field=models.TextField(blank=True)),
    ]
