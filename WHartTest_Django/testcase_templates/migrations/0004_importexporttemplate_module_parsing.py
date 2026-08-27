from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('testcase_templates', '0003_importexporttemplate_template_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='importexporttemplate',
            name='module_hierarchy_columns',
            field=models.JSONField(blank=True, default=list, help_text='按层级顺序保存Excel表头，如 ["一级模块", "二级模块"]', verbose_name='模块层级表头'),
        ),
        migrations.AddField(
            model_name='importexporttemplate',
            name='module_parsing_mode',
            field=models.CharField(choices=[('path', '单列路径'), ('columns', '多列表头层级')], default='path', help_text='单列路径或按多个Excel表头列构建模块层级', max_length=20, verbose_name='模块解析模式'),
        ),
    ]
