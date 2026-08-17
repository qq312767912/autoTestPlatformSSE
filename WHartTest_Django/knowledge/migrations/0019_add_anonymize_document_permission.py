# Generated migration for adding anonymize_document permission

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0018_add_anonymization_fields'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='knowledgebase',
            options={
                'ordering': ['-created_at'],
                'permissions': [
                    ('anonymize_document', 'Can anonymize documents in knowledge base'),
                ],
                'verbose_name': '知识库',
                'verbose_name_plural': '知识库',
            },
        ),
    ]
