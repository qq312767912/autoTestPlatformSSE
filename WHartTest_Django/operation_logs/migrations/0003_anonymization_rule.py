from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('operation_logs', '0002_operationlogsetting_cleanup_task'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnonymizationRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='规则的唯一标识名称', max_length=100, unique=True, verbose_name='规则名称')),
                ('entity_type', models.CharField(help_text='PII 类型，如 PHONE_NUMBER、EMAIL_ADDRESS', max_length=50, verbose_name='实体类型')),
                ('entity_label', models.CharField(help_text='中文显示名称，如「手机号」「邮箱地址」', max_length=50, verbose_name='显示标签')),
                ('regex', models.TextField(help_text='用于匹配 PII 的正则表达式', verbose_name='正则表达式')),
                ('score', models.FloatField(default=0.8, help_text='匹配置信度，范围 0.0~1.0', validators=[django.core.validators.MinValueValidator(0.0), django.core.validators.MaxValueValidator(1.0)], verbose_name='置信度')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('description', models.TextField(blank=True, default='', verbose_name='规则说明')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '脱敏规则',
                'verbose_name_plural': '脱敏规则',
                'ordering': ['-is_active', 'entity_type'],
            },
        ),
    ]
