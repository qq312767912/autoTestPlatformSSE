from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('api_testcases','0005_apiinterfacecase_apiinterfacecasereport_and_more'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='ApiExcelImportArchive',
        fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('sha256',models.CharField(max_length=64)),('file_name',models.CharField(max_length=255)),
            ('encrypted_payload',models.TextField()),('summary',models.JSONField(default=dict)),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('created_by',models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL)),
            ('project',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='api_excel_imports',to='projects.project')),
        ],options={'unique_together':{('project','sha256')}})]
