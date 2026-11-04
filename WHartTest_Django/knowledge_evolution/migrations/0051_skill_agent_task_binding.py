from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('knowledge_evolution', '0050_hub_skill_assessment'), ('langgraph_integration', '0033_llm_auth_state_binding')]
    operations = [migrations.CreateModel(name='SkillAgentTaskBinding', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('source_sha256', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('session', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='langgraph_integration.chatsession')),
        ('source_version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='skills.skillversion')),
    ])]
