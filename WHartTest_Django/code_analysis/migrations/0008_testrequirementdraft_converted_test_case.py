from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("testcases", "0022_alter_testcasestep_expected_result"),
        ("code_analysis", "0007_analysistask_executor"),
    ]

    operations = [
        migrations.AddField(
            model_name="testrequirementdraft",
            name="converted_test_case",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="code_review_drafts", to="testcases.testcase"),
        ),
    ]
