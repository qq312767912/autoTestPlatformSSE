from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemconfig',
            name='login_tags',
            field=models.TextField(
                default='AI 智能生成, RAG 知识库, MCP 工具调用, Skills 技能库, Playwright 自动化, LangGraph, 代码审查, 知识图谱, 数据飞轮, Skill 自进化',
                help_text='登录页面显示的平台特色标签，用逗号或中文逗号分隔',
                verbose_name='登录页特色标签',
            ),
        ),
    ]
