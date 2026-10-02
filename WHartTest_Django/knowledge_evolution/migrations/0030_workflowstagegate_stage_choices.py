# 手写迁移：``knowledge_evolution`` 在本地开发环境是只读挂载，
# ``makemigrations`` 无法回写该目录，因此按 Django 生成格式手写。
#
# 改动内容只有字段 ``choices``，**不涉及数据库结构**（``stage`` 仍是
# ``varchar(32)``，``choices`` 只是应用层校验 + 表单/后台展示）。
# 所以这次迁移在库层面是空操作，但它必须存在：否则
# ``makemigrations --check`` 会一直报「有未生成的迁移」，
# 后续任何一次真正的模型改动都会把这条差异混进去，掩盖真实变更。
#
# 口径变更（2026-10-02）：主链路改为
# ``risk_identification -> testcase_generation -> test_execution -> issue_tracking``，
# 因此把新链路的两个阶段补进 ``stage`` 的合法取值；历史两阶段**保留**，
# 存量流程的门禁记录仍然要能被读出来、继续推进。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_evolution', '0029_workflowstagegate_status_choices'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflowstagegate',
            name='stage',
            field=models.CharField(
                choices=[
                    # 新主链路四阶段
                    ('risk_identification', '风险识别'),
                    ('testcase_generation', '测试用例生成'),
                    ('test_execution', '测试执行'),
                    ('issue_tracking', '问题跟踪'),
                    # 历史主链路两阶段（降为单次能力，但存量流程仍在用）
                    ('test_plan_generation', '测试方案生成'),
                    ('report_generation', '报告生成'),
                ],
                db_index=True, max_length=32,
            ),
        ),
    ]
