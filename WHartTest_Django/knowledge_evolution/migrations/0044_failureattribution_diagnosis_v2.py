from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("knowledge_evolution", "0043_flywheelregistrationfailure")]

    operations = [
        migrations.AddField(
            model_name="failureattribution", name="primary_cause",
            field=models.CharField(blank=True, db_index=True, max_length=40),
        ),
        migrations.AddField(
            model_name="failureattribution", name="fine_cause",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="failureattribution", name="repair_target",
            field=models.CharField(blank=True, db_index=True, max_length=32),
        ),
        migrations.AddField(
            model_name="failureattribution", name="evidence_status",
            field=models.CharField(db_index=True, default="unverified", max_length=20),
        ),
        migrations.AddField(
            model_name="failureattribution", name="impact_scope",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="failureattribution", name="diagnosis_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddIndex(
            model_name="failureattribution",
            index=models.Index(fields=["project", "primary_cause", "state"], name="ke_attr_primary_state_idx"),
        ),
        migrations.AddIndex(
            model_name="failureattribution",
            index=models.Index(fields=["project", "repair_target", "state"], name="ke_attr_repair_state_idx"),
        ),
    ]
