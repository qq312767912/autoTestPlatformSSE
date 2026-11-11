from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('requirements', '0013_requirementdocument_document_category')]

    operations = [
        migrations.AddField(
            model_name='requirementdocument',
            name='management_mode',
            field=models.CharField(
                choices=[('ordinary', '普通文档'), ('consolidated', '一本化文档')],
                default='ordinary', max_length=20, verbose_name='文档管理方式',
            ),
        ),
    ]
