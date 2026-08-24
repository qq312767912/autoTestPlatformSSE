from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('requirements', '0010_add_anonymization_fields')]

    operations = [
        migrations.AddField(
            model_name='requirementdocument',
            name='image_analysis_status',
            field=models.CharField(choices=[('not_started', '未开始'), ('processing', '分析中'), ('user_reviewing', '用户调整中'), ('confirmed', '已确认'), ('failed', '分析失败')], default='not_started', max_length=20, verbose_name='图片分析状态'),
        ),
        migrations.AddField(model_name='documentimage', name='module', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='document_images', to='requirements.requirementmodule', verbose_name='所属需求模块')),
        migrations.AddField(model_name='documentimage', name='nearby_text', field=models.TextField(blank=True, verbose_name='图片附近文字')),
        migrations.AddField(model_name='documentimage', name='ocr_text', field=models.TextField(blank=True, verbose_name='OCR文字')),
        migrations.AddField(model_name='documentimage', name='page_title', field=models.CharField(blank=True, max_length=255, verbose_name='页面名称')),
        migrations.AddField(model_name='documentimage', name='change_type', field=models.CharField(choices=[('add', '新增'), ('change', '修改'), ('remove', '删除'), ('unknown', '无法判断')], default='unknown', max_length=20, verbose_name='变更类型')),
        migrations.AddField(model_name='documentimage', name='change_description', field=models.TextField(blank=True, verbose_name='变更说明')),
        migrations.AddField(model_name='documentimage', name='analysis_result', field=models.JSONField(blank=True, default=dict, verbose_name='结构化分析结果')),
        migrations.AddField(model_name='documentimage', name='suggested_test_points', field=models.JSONField(blank=True, default=list, verbose_name='建议测试点')),
        migrations.AddField(model_name='documentimage', name='confidence', field=models.FloatField(blank=True, null=True, verbose_name='识别置信度')),
        migrations.AddField(model_name='documentimage', name='user_notes', field=models.TextField(blank=True, verbose_name='用户备注')),
        migrations.AddField(model_name='documentimage', name='is_enabled', field=models.BooleanField(default=True, verbose_name='是否采用')),
        migrations.AddField(model_name='documentimage', name='review_status', field=models.CharField(choices=[('pending', '待分析'), ('analyzed', '待确认'), ('confirmed', '已确认'), ('ignored', '已忽略'), ('error', '分析失败')], default='pending', max_length=20, verbose_name='图片确认状态')),
        migrations.AddField(model_name='documentimage', name='analysis_error', field=models.TextField(blank=True, verbose_name='分析错误')),
        migrations.AddField(model_name='documentimage', name='updated_at', field=models.DateTimeField(auto_now=True, null=True, verbose_name='更新时间')),
        migrations.AlterField(model_name='documentimage', name='updated_at', field=models.DateTimeField(auto_now=True, verbose_name='更新时间')),
    ]
