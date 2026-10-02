# 手写迁移：``knowledge_evolution`` 在本地开发环境是只读挂载，
# ``makemigrations`` 无法回写该目录，因此按 Django 生成格式手写。
#
# 改动内容只有字段 ``choices`` 的**取值与顺序**，**不涉及数据库结构**
# （``stage`` 仍是 ``varchar(32)``，``choices`` 只是应用层校验 + 表单/后台展示）。
# 所以这次迁移在库层面是空操作，但它必须存在：否则 ``makemigrations --check``
# 会一直报「有未生成的迁移」，后续任何一次真正的模型改动都会把这条差异混进去。
#
# 口径回退（2026-10-02）：主链路恢复为
# ``test_plan_generation -> testcase_generation -> test_execution -> report_generation``
# （方案生成 → 用例生成 → 测试执行 → 报告产出）。``choices`` 由
# ``ALL_WORKFLOW_STAGES`` 派生，顺序即"当前链路在前、历史模板在后"，
# 所以本次只是把 ``0030`` 里那两组调了个位置：合法取值集合**没有变化**，
# 中间模板期间留下的门禁记录仍然读得出来。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_evolution', '0030_workflowstagegate_stage_choices'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflowstagegate',
            name='stage',
            field=models.CharField(
                choices=[
                    # 当前主链路四阶段
                    ('test_plan_generation', '测试方案生成'),
                    ('testcase_generation', '测试用例生成'),
                    ('test_execution', '测试执行'),
                    ('report_generation', '报告生成'),
                    # 历史模板两阶段（单次能力，但那批流程的门禁仍在用）
                    ('risk_identification', '风险识别'),
                    ('issue_tracking', '问题跟踪'),
                ],
                db_index=True, max_length=32,
            ),
        ),
    ]
