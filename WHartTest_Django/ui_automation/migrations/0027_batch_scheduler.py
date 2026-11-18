from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [('ui_automation','0026_uitestcase_role_matrix')]
    operations = [
        migrations.AddField(model_name='uibatchexecutionrecord',name='run_config',field=models.JSONField(default=dict,blank=True,verbose_name='调度配置')),
        migrations.AddField(model_name='uibatchexecutionrecord',name='run_items',field=models.JSONField(default=list,blank=True,verbose_name='调度进度')),
        migrations.AddField(model_name='uibatchexecutionrecord',name='cancel_requested',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='uibatchexecutionrecord',name='heartbeat_at',field=models.DateTimeField(null=True,blank=True)),
    ]
