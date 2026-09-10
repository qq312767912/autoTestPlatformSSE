from django.db import migrations, models


def disable_existing_gitlab_ssl_verification(apps, schema_editor):
    GitLabConnection = apps.get_model("code_analysis", "GitLabConnection")
    GitLabConnection.objects.filter(verify_ssl=True).update(verify_ssl=False)


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0009_analysistask_context_document_ids_multi")]

    operations = [
        migrations.AlterField(
            model_name="gitlabconnection",
            name="verify_ssl",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.RunPython(
            disable_existing_gitlab_ssl_verification,
            migrations.RunPython.noop,
        ),
    ]
