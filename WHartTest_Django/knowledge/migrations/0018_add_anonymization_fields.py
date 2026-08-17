from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0017_remove_document_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='is_anonymized',
            field=models.BooleanField(default=False, verbose_name='已脱敏'),
        ),
        migrations.AddField(
            model_name='document',
            name='anonymized_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='脱敏时间'),
        ),
    ]
