from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0018_optimizationproposal_optimizationexperiment_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaluationcase", name="split",
            field=models.CharField(choices=[("gold", "Gold"), ("regression", "Regression"), ("fresh", "Fresh"), ("challenge", "Challenge"), ("hidden", "Hidden")], db_index=True, default="regression", max_length=20),
        ),
        migrations.AlterField(
            model_name="evaluationsuite", name="suite_type",
            field=models.CharField(choices=[("seed", "种子集"), ("gold", "金标集"), ("regression", "回归集"), ("fresh", "新鲜集"), ("challenge", "挑战集")], db_index=True, max_length=20),
        ),
    ]
