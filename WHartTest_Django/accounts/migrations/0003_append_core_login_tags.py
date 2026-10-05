from django.db import migrations


REQUIRED_TAGS = ("代码审查", "知识图谱", "数据飞轮")


def append_core_tags(apps, schema_editor):
    SystemConfig = apps.get_model("accounts", "SystemConfig")
    for config in SystemConfig.objects.all().iterator():
        tags = [item.strip() for item in (config.login_tags or "").replace("，", ",").split(",") if item.strip()]
        changed = False
        for tag in REQUIRED_TAGS:
            if tag not in tags:
                tags.append(tag)
                changed = True
        if changed:
            config.login_tags = ", ".join(tags)
            config.save(update_fields=["login_tags"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_alter_systemconfig_login_tags")]
    operations = [migrations.RunPython(append_core_tags, migrations.RunPython.noop)]
