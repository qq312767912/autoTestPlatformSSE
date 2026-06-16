from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import ApiSyncConfig, ApiSyncHistory
from .tasks import sync_interface_data


@receiver(post_save, sender='api_interfaces.ApiInterface')
def handle_interface_update(sender, instance, created, **kwargs):
    """Automatically sync when an interface is updated (not created)."""
    if created:
        return

    sync_configs = ApiSyncConfig.objects.filter(
        interface=instance,
        sync_enabled=True,
        sync_mode='auto',
    ).select_related('step')

    for config in sync_configs:
        try:
            trigger = config.sync_trigger or {}
            fields_to_watch = trigger.get('fields_to_watch', []) or config.sync_fields

            step = config.step
            old_data = {
                field: step.interface_data.get(field)
                for field in config.sync_fields
            }

            interface_data = {
                'method': instance.method,
                'url': instance.url,
                'headers': instance.headers,
                'params': instance.params,
                'body': instance.body,
                'setup_hooks': instance.setup_hooks,
                'teardown_hooks': instance.teardown_hooks,
                'variables': instance.variables,
                'validators': instance.validators,
                'extract': instance.extract,
            }

            has_changes = any(
                old_data.get(field) != interface_data.get(field)
                for field in fields_to_watch
            )

            if has_changes:
                sync_interface_data.delay(config.id)

        except Exception as e:
            ApiSyncHistory.objects.create(
                sync_config=config,
                sync_type='auto',
                sync_status='failed',
                sync_fields=config.sync_fields,
                old_data={},
                new_data={},
                error_message=str(e),
                operator=None,
            )
