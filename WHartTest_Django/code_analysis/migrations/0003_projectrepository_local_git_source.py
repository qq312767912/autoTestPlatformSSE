from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0002_analysistask_celery_task_id")]

    operations = [
        migrations.AddField(
            model_name="projectrepository",
            name="source_type",
            field=models.CharField(choices=[("gitlab", "GitLab"), ("local_git", "本地 Git（开发测试）")], default="gitlab", max_length=20),
        ),
        migrations.AddField(model_name="projectrepository", name="local_path", field=models.CharField(blank=True, max_length=500)),
        migrations.AlterField(model_name="projectrepository", name="connection", field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="repositories", to="code_analysis.gitlabconnection")),
        migrations.AlterField(model_name="projectrepository", name="gitlab_project_id", field=models.CharField(blank=True, max_length=255)),
    ]
