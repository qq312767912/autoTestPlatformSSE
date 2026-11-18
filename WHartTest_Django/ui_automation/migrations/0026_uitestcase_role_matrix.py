from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('ui_automation', '0025_alter_uicasestepsdetailed_page_step_and_more')]
    operations = [migrations.AddField(model_name='uitestcase', name='role_matrix', field=models.JSONField(default=list, blank=True, verbose_name='执行角色'))]
