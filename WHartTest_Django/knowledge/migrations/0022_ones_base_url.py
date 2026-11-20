from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('knowledge', '0021_document_file_length')]
    operations = [migrations.AddField(model_name='knowledgeglobalconfig', name='ones_base_url', field=models.CharField(default='https://10.122.212.49:60000', max_length=500, verbose_name='ONES 服务地址'))]
