# Generated migration for adding AnonymizedDocument model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('operation_logs', '0003_anonymization_rule'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnonymizedDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_file', models.FileField(help_text='用户上传的原始文件', upload_to='anonymization/original/%Y%m%d/', verbose_name='原始文件')),
                ('original_filename', models.CharField(max_length=255, verbose_name='原始文件名')),
                ('file_type', models.CharField(help_text='.txt, .md, .docx', max_length=10, verbose_name='文件类型')),
                ('file_size', models.IntegerField(default=0, verbose_name='文件大小(bytes)')),
                ('status', models.CharField(choices=[('pending', '待脱敏'), ('anonymizing', '脱敏中'), ('anonymized', '已脱敏'), ('failed', '脱敏失败')], db_index=True, default='pending', max_length=20, verbose_name='脱敏状态')),
                ('anonymized_file', models.FileField(blank=True, null=True, upload_to='anonymization/result/%Y%m%d/', verbose_name='脱敏后文件')),
                ('anonymized_at', models.DateTimeField(blank=True, null=True, verbose_name='脱敏时间')),
                ('anonymization_report', models.JSONField(blank=True, help_text='包含 total_count, details 等脱敏统计信息', null=True, verbose_name='脱敏报告')),
                ('error_message', models.TextField(blank=True, default='', verbose_name='错误信息')),
                ('enabled_preset_types', models.JSONField(blank=True, default=list, help_text="如 ['PHONE_NUMBER', 'EMAIL_ADDRESS', 'ID_CARD']", verbose_name='启用的预设PII类型')),
                ('custom_keywords', models.JSONField(blank=True, default=list, help_text="如 ['张三', '北京科技有限公司']", verbose_name='自定义关键词列表')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='上传时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('uploaded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='anonymized_documents', to=settings.AUTH_USER_MODEL, verbose_name='上传者')),
            ],
            options={
                'verbose_name': '脱敏文档',
                'verbose_name_plural': '脱敏文档',
                'ordering': ['-created_at'],
            },
        ),
    ]
