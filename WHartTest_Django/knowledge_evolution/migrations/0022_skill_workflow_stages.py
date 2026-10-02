from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge_evolution", "0021_workflowstagegate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowstagegate",
            name="stage",
            field=models.CharField(
                choices=[
                    ("test_plan_generation", "测试方案生成"),
                    ("testcase_generation", "测试用例生成"),
                    ("test_execution", "测试执行"),
                    ("report_generation", "报告生成"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="evaluationsuite",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("knowledge_query", "知识库问答"),
                    ("case_review", "用例审查"),
                    ("code_review", "代码审查"),
                    ("risk_identification", "风险识别"),
                    ("test_plan_generation", "测试方案生成"),
                    ("test_execution", "测试执行"),
                    ("testcase_generation", "用例生成"),
                    ("issue_tracking", "问题跟踪"),
                    ("report_generation", "报告生成"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="evaluationcase",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("knowledge_query", "知识库问答"),
                    ("case_review", "用例审查"),
                    ("code_review", "代码审查"),
                    ("risk_identification", "风险识别"),
                    ("test_plan_generation", "测试方案生成"),
                    ("test_execution", "测试执行"),
                    ("testcase_generation", "用例生成"),
                    ("issue_tracking", "问题跟踪"),
                    ("report_generation", "报告生成"),
                ],
                max_length=32,
            ),
        ),
    ]
