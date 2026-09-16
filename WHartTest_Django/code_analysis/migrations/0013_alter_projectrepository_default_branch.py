from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0012_codeanalysisllmconfig")]

    operations = [
        migrations.AlterField(
            model_name="projectrepository",
            name="default_branch",
            field=models.CharField(default="master", max_length=255),
        ),
    ]
