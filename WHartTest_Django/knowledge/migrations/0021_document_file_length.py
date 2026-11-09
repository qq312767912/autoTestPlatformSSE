from django.db import migrations, models
import knowledge.models


class Migration(migrations.Migration):
    dependencies = [('knowledge', '0020_documentchunk_block_type_documentchunk_location_and_more')]
    operations = [migrations.AlterField(
        model_name='document', name='file',
        field=models.FileField(blank=True, null=True, max_length=500,
                               upload_to=knowledge.models.document_upload_path, verbose_name='文件'),
    )]
