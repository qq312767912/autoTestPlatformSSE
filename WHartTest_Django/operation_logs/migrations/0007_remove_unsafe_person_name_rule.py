from django.db import migrations


REMOVED_ENTITY_TYPES = {"PERSON", "PERSON_NAME"}


def remove_unsafe_person_name_rule(apps, schema_editor):
    AnonymizationRule = apps.get_model("operation_logs", "AnonymizationRule")
    AnonymizedDocument = apps.get_model("operation_logs", "AnonymizedDocument")
    AnonymizationTemplate = apps.get_model("operation_logs", "AnonymizationTemplate")

    AnonymizationRule.objects.filter(
        entity_type__in=REMOVED_ENTITY_TYPES
    ).delete()

    for model in (AnonymizedDocument, AnonymizationTemplate):
        queryset = model.objects.all().only("id", "enabled_preset_types")
        for instance in queryset.iterator():
            enabled_types = instance.enabled_preset_types or []
            cleaned_types = [
                entity_type
                for entity_type in enabled_types
                if entity_type not in REMOVED_ENTITY_TYPES
            ]
            if cleaned_types != enabled_types:
                instance.enabled_preset_types = cleaned_types
                instance.save(update_fields=["enabled_preset_types"])


class Migration(migrations.Migration):
    dependencies = [
        ("operation_logs", "0006_anonymizationtemplate"),
    ]

    operations = [
        migrations.RunPython(remove_unsafe_person_name_rule, migrations.RunPython.noop),
    ]
