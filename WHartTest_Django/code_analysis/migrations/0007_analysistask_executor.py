from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0006_analysistaskexecutionlog")]

    operations = [
        migrations.AddField(
            model_name="analysistask",
            name="executor",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="executed_code_analysis_tasks", to=settings.AUTH_USER_MODEL),
        ),
    ]
