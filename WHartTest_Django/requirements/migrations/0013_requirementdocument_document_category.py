from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('requirements', '0012_documentimage_table_markdown'),
    ]

    operations = [
        migrations.AddField(
            model_name='requirementdocument',
            name='document_category',
            field=models.CharField(
                choices=[
                    ('business_requirement', '业务需求文档'),
                    ('requirement_specification', '需求规格说明书'),
                    ('technical_design', '技术设计文档'),
                ],
                db_index=True,
                default='business_requirement',
                max_length=40,
                verbose_name='文档分类',
            ),
        ),
    ]
