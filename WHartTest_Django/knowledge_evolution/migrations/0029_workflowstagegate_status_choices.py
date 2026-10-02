# 手写迁移：``knowledge_evolution`` 在本地开发环境是只读挂载，
# ``makemigrations`` 无法回写该目录，因此按 Django 生成格式手写。
#
# 改动内容只有字段 ``choices``，**不涉及数据库结构**（``status`` 仍是
# ``varchar(20)``，``choices`` 只是应用层校验 + 表单/后台展示）。
# 所以这次迁移在库层面是空操作，但它必须存在：否则
# ``makemigrations --check`` 会一直报「有未生成的迁移」，
# 后续任何一次真正的模型改动都会把这条差异混进去，掩盖真实变更。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_evolution', '0028_workflowstagegate_detail'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflowstagegate',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', '待测评'),
                    # 新增：没有可用评分（信号缺失）。与 failed（结论为负）
                    # 严格区分——前者可由人工确认放行，后者必须强制放行。
                    ('unscored', '无评分'),
                    ('passed', '测评通过'),
                    ('failed', '测评失败'),
                    # 新增：人工确认放行。不要求先有评分，确认后直接进入下一阶段。
                    ('confirmed', '人工确认'),
                    ('overridden', '负责人放行'),
                ],
                db_index=True, default='pending', max_length=20,
            ),
        ),
    ]
