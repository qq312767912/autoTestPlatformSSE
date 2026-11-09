"""Keep conversion provenance separate from editable business descriptions."""
import json
import re
from django.db import migrations, models


def separate_legacy_descriptions(apps, schema_editor):
    Case = apps.get_model('ui_automation', 'UiTestCase')
    for case in Case.objects.using(schema_editor.connection.alias).filter(description__startswith='[business-to-ui:').iterator():
        text = case.description or ''
        lines = text.splitlines()
        if not re.fullmatch(r'\[business-to-ui:[0-9a-f]{64}\]', lines[0]) or len(lines) < 3 or lines[1] != '[excel-to-ui-case]' or not lines[2].startswith('source_key=testcase-management|'):
            continue
        identity = lines[2].removeprefix('source_key=')
        suffix = '\n原始业务用例（完整保留，凭据脱敏）：\n'
        body, separator, raw = text.partition(suffix)
        # Only split known generated records. Preserve uncertain/manual content verbatim.
        if not separator:
            continue
        try:
            source = json.loads(raw)
        except (ValueError, TypeError):
            continue
        skill = re.search(r'^Skill：(.*?)；版本 SHA256：([0-9a-f]{64})$', body, re.M)
        fingerprint = re.search(r'^来源内容SHA256=([0-9a-f]{64})$', body, re.M)
        model = re.search(r'^转换模型：(.*)$', body, re.M)
        if not skill or not fingerprint:
            continue
        clean = []
        for line in body.splitlines()[3:]:
            if line.startswith(('来源：测试用例管理 / ', 'Skill：', '转换模型：', '来源内容SHA256=', '转换状态：', '缺口：')) or line == '角色：未指定':
                continue
            clean.append('操作步骤：' if line == '转换步骤：' else line)
        description = '\n'.join(clean)
        case.conversion_metadata = {
            'source_key': identity, 'source_fingerprint': fingerprint[1],
            'skill': {'name': skill[1], 'sha256': skill[2]},
            'model': {'name': model[1] if model else ''}, 'source': source,
            'legacy_description': text, 'migrated_description': description,
        }
        case.description = description
        case.save(using=schema_editor.connection.alias, update_fields=['description', 'conversion_metadata'])


def restore_legacy_descriptions(apps, schema_editor):
    Case = apps.get_model('ui_automation', 'UiTestCase')
    for case in Case.objects.using(schema_editor.connection.alias).exclude(conversion_metadata={}).iterator():
        metadata = case.conversion_metadata
        if metadata.get('legacy_description') and case.description == metadata.get('migrated_description'):
            case.description = metadata['legacy_description']
            case.save(using=schema_editor.connection.alias, update_fields=['description'])


class Migration(migrations.Migration):
    dependencies = [('ui_automation', '0023_uipagesteps_auth_state')]
    operations = [
        migrations.AddField(model_name='uitestcase', name='conversion_metadata',
                            field=models.JSONField(blank=True, default=dict, editable=False, verbose_name='转换追溯信息')),
        migrations.RunPython(separate_legacy_descriptions, restore_legacy_descriptions),
    ]
