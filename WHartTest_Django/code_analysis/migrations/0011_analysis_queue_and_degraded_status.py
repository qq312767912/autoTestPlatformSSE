from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("code_analysis", "0010_disable_gitlab_ssl_verification")]

    operations = [
        migrations.AlterField(
            model_name="analysistask",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待执行"), ("queued", "排队中"),
                    ("fetching", "获取代码中"), ("machine_analyzing", "机器分析中"),
                    ("ai_analyzing", "AI分析中"), ("generating_tests", "生成测试报告中"),
                    ("completed", "已完成"), ("degraded", "降级完成"),
                    ("partial", "部分完成"), ("failed", "失败"), ("cancelled", "已取消"),
                ], default="pending", max_length=30, db_index=True,
            ),
        ),
        migrations.AlterField(
            model_name="analysistaskexecutionlog",
            name="event",
            field=models.CharField(
                choices=[
                    ("created", "已创建"), ("queued", "已入队"), ("started", "已开始"),
                    ("completed", "已完成"), ("degraded", "降级完成"),
                    ("partial", "部分完成"), ("failed", "执行失败"), ("cancelled", "已取消"),
                ], max_length=20,
            ),
        ),
    ]
