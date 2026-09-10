from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("skills", "0002_alter_skill_description_alter_skill_name"), ("testcases", "0023_testcasereview")]

    operations = [
        migrations.AddField(model_name="testcasereview", name="review_mode", field=models.CharField(choices=[("general", "通用审查"), ("specified", "指定 Skill 审查")], default="general", max_length=20)),
        migrations.AddField(model_name="testcasereview", name="selected_skill", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="testcase_reviews", to="skills.skill")),
        migrations.AddField(model_name="testcasereview", name="skill_name", field=models.CharField(default="test-case-clarity-review", max_length=255)),
        migrations.AddField(model_name="testcasereview", name="skill_snapshot", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="testcasereview", name="custom_rules", field=models.TextField(blank=True, default="")),
    ]
