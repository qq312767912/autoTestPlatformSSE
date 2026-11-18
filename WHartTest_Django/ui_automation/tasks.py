from celery import shared_task


@shared_task(name='ui_automation.run_ui_healing')
def run_ui_healing(pk):
    from .healing import process_batch
    process_batch(pk)

@shared_task(name='ui_automation.run_ui_script_batch')
def run_ui_script_batch(pk):
    from .script_batches import process_script_batch
    try:
        process_script_batch(pk)
    except Exception as exc:
        from django.utils import timezone
        from .models import UiBatchExecutionRecord
        batch = UiBatchExecutionRecord.objects.filter(pk=pk,status__in=[0,1]).first()
        if batch:
            items=[{**item,'status':'failed','message':'后台调度异常：'+str(exc)[:300]} if item.get('status') in ('queued','running','waiting') else item for item in batch.run_items]
            UiBatchExecutionRecord.objects.filter(pk=pk,status__in=[0,1]).update(status=3 if any(x['status']=='passed' for x in items) else 4,run_items=items,passed_cases=sum(x['status']=='passed' for x in items),failed_cases=sum(x['status']=='failed' for x in items),end_time=timezone.now(),heartbeat_at=timezone.now())
        raise
