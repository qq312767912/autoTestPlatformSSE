from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('langgraph_integration', '0032_chatmessage_unique_session_chat_message'),
        ('ui_automation', '0023_uipagesteps_auth_state'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='auth_state',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='llm_chat_sessions', to='ui_automation.uiauthstate', verbose_name='LLM探索登录态'),
        ),
        migrations.AddField(
            model_name='chatsession',
            name='auth_state_bound_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='登录态绑定时间'),
        ),
        migrations.CreateModel(
            name='LlmAuthStateUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('auth_state_id_snapshot', models.BigIntegerField(verbose_name='登录态ID快照')),
                ('auth_state_name_snapshot', models.CharField(max_length=128, verbose_name='登录态名称快照')),
                ('target_origin', models.CharField(blank=True, default='', max_length=512)),
                ('result', models.CharField(choices=[('started', '已开始'), ('succeeded', '成功'), ('expired', '已过期'), ('login_redirect', '跳转登录页'), ('failed', '失败'), ('closed', '已关闭')], default='started', max_length=32)),
                ('error_summary', models.CharField(blank=True, default='', max_length=512)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('chat_session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='auth_state_usages', to='langgraph_integration.chatsession')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='projects.project')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-started_at']},
        ),
        migrations.AddIndex(
            model_name='llmauthstateusage',
            index=models.Index(fields=['chat_session', 'started_at'], name='langgraph_i_chat_se_53cd50_idx'),
        ),
    ]
