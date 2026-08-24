from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("requirements", "0011_add_image_analysis_workflow")]

    operations = [
        migrations.AddField(
            model_name="documentimage",
            name="table_markdown",
            field=models.TextField(blank=True, verbose_name="表格Markdown"),
        ),
    ]
