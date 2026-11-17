"""服务端顺序执行探索转换批次；前端断开不取消任务。"""
import json
import asyncio
import logging
from asgiref.sync import async_to_sync, sync_to_async
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .hub_assessment_models import SkillAgentTaskBinding


def update_run(pk, **changes):
    with transaction.atomic():
        binding = SkillAgentTaskBinding.objects.select_for_update().get(pk=pk)
        run = dict(binding.generation_run)
        run.update(changes)
        binding.generation_run = run
        binding.save(update_fields=['generation_run'])
        return run


async def consume(binding, message, parent_id=None):
    from django.test import RequestFactory
    from rest_framework_simplejwt.tokens import AccessToken
    from orchestrator_integration.agent_loop_view import AgentLoopStreamAPIView
    # 使用现有身份校验和执行链路，不创建持久 API Key，不将 Token 写入任务参数。
    def preflight():
        from .hub_assessment import verify_source
        from ui_automation.models import UiEnvironmentConfig
        verify_source(binding.source_version)
        if not binding.source_version.is_runnable:
            raise ValueError('Skill 精确版本已不可运行')
        ctx = binding.generation_context
        if not UiEnvironmentConfig.objects.filter(pk=ctx['env_config_id'], project_id=binding.session.project_id, base_url=ctx['base_url']).exists():
            raise ValueError('执行环境已变化，请重新确认')
    await sync_to_async(preflight)()
    token = await sync_to_async(lambda: str(AccessToken.for_user(binding.session.user)))()
    request = RequestFactory().post('/api/orchestrator/agent-loop/', data=json.dumps({
        'project_id': binding.session.project_id, 'session_id': binding.session.session_id,
        'skill_version_id': str(binding.source_version_id), 'auth_state_id': binding.session.auth_state_id,
        'message': message, 'stream': True,
    }), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + token)
    request._ui_conversion_worker = True
    response = await AgentLoopStreamAPIView().post(request)
    if response.status_code != 200 or not getattr(response, 'streaming', False):
        raise RuntimeError('探索任务未能启动，请检查任务配置与模型状态')
    async def monitor_cancel():
        while True:
            await asyncio.sleep(2)
            run = await sync_to_async(update_run)(parent_id, heartbeat_at=timezone.now().isoformat())
            if run.get('cancel_requested') or run.get('status') not in ('queued', 'running'):
                from orchestrator_integration.stop_signal import set_stop_signal
                set_stop_signal(binding.session.session_id)
                return
    monitor = asyncio.create_task(monitor_cancel()) if parent_id else None
    try:
        async for _ in response.streaming_content:
            pass
    finally:
        if monitor:
            monitor.cancel()
            try: await monitor
            except asyncio.CancelledError: pass
        from mcp_tools.persistent_client import mcp_session_manager
        try:
            await asyncio.wait_for(mcp_session_manager.cleanup_user_session(str(binding.session.user_id), str(binding.session.project_id), binding.session.session_id), timeout=15)
        except (Exception, asyncio.CancelledError):
            logger = logging.getLogger(__name__)
            logger.warning('MCP cleanup interrupted; preserving batch outcome session=%s', binding.session.session_id)



@shared_task(name='knowledge_evolution.run_ui_conversion')
def run_ui_conversion(pk):
    with transaction.atomic():
        locked = SkillAgentTaskBinding.objects.select_for_update().get(pk=pk)
        if locked.generation_run.get('status') != 'queued':
            return
        locked.generation_run = {**locked.generation_run, 'status': 'running', 'shared_conversation': True, 'started_at': timezone.now().isoformat(), 'heartbeat_at': timezone.now().isoformat()}
        locked.save(update_fields=['generation_run'])
    parent = SkillAgentTaskBinding.objects.select_related('session__user', 'source_version').get(pk=pk)
    results = [{'source_id': x['id'], 'name': x['name'], 'status': 'queued', 'session_id': parent.session.session_id} for x in parent.generation_context['functional_cases']]
    update_run(pk, status='running', started_at=timezone.now().isoformat())
    for index, source in enumerate(parent.generation_context['functional_cases']):
        parent.refresh_from_db(fields=['generation_run'])
        if parent.generation_run.get('cancel_requested'):
            break
        session = parent.session
        child = parent
        item = {'source_id': source['id'], 'name': source['name'], 'session_id': session.session_id, 'status': 'exploring'}
        results[index] = item
        update_run(pk, items=results, active_session_id=session.session_id, active_source_id=source['id'], verified=None)
        try:
            async_to_sync(consume)(child, f"<!-- wharttest:generation_mode=ui_case -->\n当前仅处理业务用例 {source['id']}：{source['name']}。\n读取 get_ui_generation_context，按已锁定 Skill 探索、复用维护或新建 UI 用例，保留业务断言。成功试跑后必须调用 bind_generated_ui_case；无法验证则说明原因，不得声称完成。\n业务补充：" + str(parent.generation_context.get('business_notes') or ''), parent_id=pk)
            child.refresh_from_db(fields=['generation_run'])
            verified = child.generation_run.get('verified')
            parent.refresh_from_db(fields=['generation_run'])
            if parent.generation_run.get('cancel_requested'):
                item.update(status='cancelled', message='批次已取消')
            elif verified:
                from orchestrator_integration.ui_generation_runtime import bind_generated_case
                verified = bind_generated_case(session.session_id, parent.session.user_id, parent.session.project_id, source['id'], verified['ui_test_case_id'])
                item.update(status='success', result='updated' if source.get('ui_test_case_id') else 'created', **verified)
            else:
                item.update(status='needs_attention', message='未取得真实试跑及绑定证据，请查看探索会话')
        except asyncio.CancelledError:
            item.update(status='failed', message='探索连接已中断，可重新发起')
        except Exception:
            item.update(status='failed', message='探索执行异常，请查看服务端日志与对应会话')
            logging.getLogger(__name__).exception('UI conversion failed source=%s', source['id'])
        finally:
            from orchestrator_integration.ui_generation_browser import _sessions, run_ui_browser
            import hashlib
            key = 'ui-gen-' + hashlib.sha256(session.session_id.encode()).hexdigest()[:24]
            if key in _sessions:
                try: run_ui_browser(child, 'close', [])
                except Exception: logging.getLogger(__name__).warning('UI browser cleanup failed session=%s', session.session_id)
        update_run(pk, items=results)
    run = update_run(pk, active_session_id=None, active_source_id=None)
    if run.get('cancel_requested'):
        for item in results:
            if item['status'] == 'queued': item['status'] = 'cancelled'
    status = 'cancelled' if run.get('cancel_requested') else ('completed' if all(x['status']=='success' for x in results) else 'partial_failure')
    update_run(pk, status=status, finished_at=timezone.now().isoformat(), items=results)


@transaction.atomic
def reconcile_run(binding):
    """A worker heartbeat expiring must not permanently block the source cases."""
    from django.utils.dateparse import parse_datetime
    from datetime import timedelta
    binding = SkillAgentTaskBinding.objects.select_for_update().get(pk=binding.pk)
    run = binding.generation_run
    stamp = parse_datetime(run.get('heartbeat_at') or '')
    if run.get('status') == 'running' and stamp and timezone.now() - stamp > timedelta(minutes=5):
        items = [{**item, 'status': 'failed', 'message': '后台任务已失联，请重新发起'}
                 if item.get('status') in ('queued', 'exploring') else item for item in run.get('items', [])]
        binding.generation_run = update_run(binding.pk, status='partial_failure', items=items,
            active_session_id=None, error='后台任务已失联，已保留现有资产与证据，可以重新发起', finished_at=timezone.now().isoformat())
    return binding
