from celery import shared_task
from django.utils import timezone
from django.db import transaction
from django.contrib.auth.models import User

from .models import ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig


@shared_task
def sync_interface_data(config_id, user_id=None, interface_id=None, step_id=None, sync_type='auto'):
    """Execute a single sync operation (async via Celery)."""
    try:
        config = ApiSyncConfig.objects.filter(id=config_id).first() if config_id else None

        if not config:
            # Scope global config lookup to the interface's project
            from api_interfaces.models import ApiInterface
            try:
                iface = ApiInterface.objects.get(id=interface_id) if interface_id else None
            except ApiInterface.DoesNotExist:
                return {'status': 'error', 'message': f'Interface {interface_id} not found.'}
            if not iface:
                return {'status': 'error', 'message': 'No config and no interface_id provided.'}
            global_config = ApiGlobalSyncConfig.objects.filter(
                project=iface.project, is_active=True,
            ).first()
            if not global_config or not global_config.sync_enabled:
                return {'status': 'error', 'message': 'No valid sync config found.'}
            sync_enabled = global_config.sync_enabled
            sync_fields = global_config.sync_fields
            sync_mode = global_config.sync_mode
        else:
            sync_enabled = config.sync_enabled
            sync_fields = config.sync_fields
            sync_mode = config.sync_mode
            interface_id = config.interface_id
            step_id = config.step_id

        if not sync_enabled:
            return {'status': 'error', 'message': 'Sync config is disabled.'}

        if not interface_id or not step_id:
            return {'status': 'error', 'message': 'Missing interface_id or step_id.'}

        with transaction.atomic():
            from api_interfaces.models import ApiInterface
            from api_testcases.models import ApiTestCaseStep

            try:
                interface = ApiInterface.objects.get(id=interface_id)
            except ApiInterface.DoesNotExist:
                return {'status': 'error', 'message': f'Interface {interface_id} not found.'}

            interface_data = {
                'method': interface.method,
                'url': interface.url,
                'headers': interface.headers,
                'params': interface.params,
                'body': interface.body,
                'setup_hooks': interface.setup_hooks,
                'teardown_hooks': interface.teardown_hooks,
                'variables': interface.variables,
                'validators': interface.validators,
                'extract': interface.extract,
            }

            sync_data = {
                field: interface_data[field]
                for field in sync_fields
                if field in interface_data
            }

            try:
                step = ApiTestCaseStep.objects.get(id=step_id)
            except ApiTestCaseStep.DoesNotExist:
                return {'status': 'error', 'message': f'Step {step_id} not found.'}

            old_data = {
                field: step.interface_data.get(field)
                for field in sync_fields
            }

            step.interface_data.update(sync_data)
            step.last_sync_time = timezone.now()
            step.save()

            operator = User.objects.filter(id=user_id).first() if user_id else None

            if not config:
                config, _ = ApiSyncConfig.objects.get_or_create(
                    interface_id=interface_id,
                    testcase=step.testcase,
                    step_id=step_id,
                    defaults={
                        'name': f'Auto-created config - {interface.name}',
                        'sync_fields': sync_fields,
                        'sync_enabled': True,
                        'sync_mode': sync_mode,
                        'created_by': operator,
                    },
                )

            history = ApiSyncHistory.objects.create(
                sync_config=config,
                sync_type=sync_type,
                sync_status='success',
                sync_fields=sync_fields,
                old_data=old_data,
                new_data=sync_data,
                operator=operator,
            )

            return {
                'status': 'success',
                'config_id': config.id,
                'history_id': history.id,
            }

    except Exception as e:
        return {'status': 'error', 'message': f'Sync failed: {e}'}


@shared_task
def batch_sync_interface_data(config_ids, user_id=None, interface_step_pairs=None, sync_type='batch'):
    """Execute multiple sync operations in sequence."""
    success_count = 0
    failed_count = 0
    results = []

    if config_ids:
        for cid in config_ids:
            result = sync_interface_data(cid, user_id, sync_type=sync_type)
            if result.get('status') == 'success':
                success_count += 1
            else:
                failed_count += 1
            results.append({'config_id': cid, 'result': result})

    if interface_step_pairs:
        for pair in interface_step_pairs:
            iid = pair.get('interface_id')
            sid = pair.get('step_id')
            if iid and sid:
                result = sync_interface_data(None, user_id, iid, sid, sync_type=sync_type)
                if result.get('status') == 'success':
                    success_count += 1
                else:
                    failed_count += 1
                results.append({
                    'interface_id': iid,
                    'step_id': sid,
                    'result': result,
                })

    return {
        'status': 'success',
        'success_count': success_count,
        'failed_count': failed_count,
        'results': results,
    }
