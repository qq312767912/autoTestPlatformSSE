from celery import shared_task


@shared_task(name='ui_automation.run_ui_healing')
def run_ui_healing(pk):
    from .healing import process_batch
    process_batch(pk)
