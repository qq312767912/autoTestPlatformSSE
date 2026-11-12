from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import FileReference, FileAsset
from .services import maybe_cleanup_unreferenced_files


def _is_project_deletion(kwargs):
    from projects.models import Project
    origin = kwargs.get('origin')
    return isinstance(origin, Project) or getattr(origin, 'model', None) is Project


@receiver(post_delete, sender='api_interfaces.ApiInterface')
def on_api_interface_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_API_INTERFACE,
        ref_id=str(instance.id)
    ).delete()


@receiver(post_delete, sender='api_testcases.ApiTestCase')
def on_api_testcase_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_API_TESTCASE,
        ref_id=str(instance.id)
    ).delete()


@receiver(post_delete, sender='ui_automation.UiTestCase')
def on_ui_testcase_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_UI_TESTCASE,
        ref_id=str(instance.id)
    ).delete()


@receiver(post_delete, sender='ui_automation.UiPageSteps')
def on_ui_page_steps_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_UI_PAGE_STEPS,
        ref_id=str(instance.id)
    ).delete()


@receiver(post_delete, sender='ui_automation.UiPageStepsDetailed')
def on_ui_page_steps_detailed_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_UI_PAGE_STEPS,
        ref_id=f"detail:{instance.id}"
    ).delete()


@receiver(post_delete, sender='langgraph_integration.ChatSession')
def on_chat_session_deleted(sender, instance, **kwargs):
    if _is_project_deletion(kwargs):
        return
    FileReference.objects.filter(
        ref_type=FileReference.REF_LLM_CHAT,
        ref_id=getattr(instance, 'session_id', '')
    ).delete()


@receiver(post_delete, sender=FileReference)
def on_file_reference_deleted(sender, instance, **kwargs):
    # 项目整体级联删除时不重建设置，也不执行独立解绑清理。
    if _is_project_deletion(kwargs):
        return
    file_asset = instance.file
    project = instance.project
    if FileAsset.objects.filter(id=file_asset.id).exists():
        maybe_cleanup_unreferenced_files(project, candidate_file_ids=[file_asset.id], reason='unbind')
        maybe_cleanup_unreferenced_files(project, candidate_file_ids=[file_asset.id], reason='zero_refs')
