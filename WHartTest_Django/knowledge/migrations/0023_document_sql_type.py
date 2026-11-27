from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('knowledge', '0022_ones_base_url')]
    operations = [
        migrations.AlterField(
            model_name='document',
            name='document_type',
            field=models.CharField(choices=[('pdf', 'PDF'), ('docx', 'Word文档'), ('doc', 'Word文档(旧版)'), ('xlsx', 'Excel表格'), ('xls', 'Excel表格(旧版)'), ('pptx', 'PowerPoint'), ('txt', '文本文件'), ('md', 'Markdown'), ('sql', 'SQL'), ('html', 'HTML'), ('url', '网页链接')], max_length=10, verbose_name='文档类型'),
        ),
    ]
