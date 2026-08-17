from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('requirements', '0009_add_last_split_level'),
    ]

    operations = [
        migrations.AddField(
            model_name='requirementdocument',
            name='is_anonymized',
            field=models.BooleanField(default=False, verbose_name='已脱敏'),
        ),
        migrations.AddField(
            model_name='requirementdocument',
            name='anonymized_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='脱敏时间'),
        ),
    ]
