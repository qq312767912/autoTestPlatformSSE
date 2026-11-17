"""持久化 UI 批量自愈：脚本首跑 → 失败项 AI 修复 → 服务端重跑取证。"""
import asyncio
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import quote
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import get_object_or_404
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from knowledge_evolution.ui_generation_jobs import update_run, consume, reconcile_run
from orchestrator_integration.ui_generation_runtime import assertion_snapshot
from .models import UiTestCase, UiEnvironmentConfig, UiPageSteps

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')


def healing_version(project, actor):
    from skills.models import SkillVersion
    from skills.versions import SkillVersionService
    from knowledge_evolution.hub_assessment import verify_source
    root = Path(settings.BASE_DIR) / 'platform_skills' / 'ui-case-healing'
    if not (root / 'SKILL.md').is_file():
        root = Path(settings.BASE_DIR) / 'bundled_skills' / 'ui-case-healing'
    if not (root / 'SKILL.md').is_file():
        raise ValidationError('平台自愈 Skill 文件缺失，请更新服务')
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()
    version = SkillVersion.objects.filter(skill__name='ui-case-healing', source_metadata__builtin_sha256=fingerprint).order_by('-created_at').first()
    if not version:
        _, version = SkillVersionService.create_candidate_from_dir(source_dir=str(root), project=project, actor=actor,
            version_override='0.0.0-builtin.' + fingerprint[:12],
            source_metadata={'platform_builtin': 'ui-case-healing', 'builtin_sha256': fingerprint}, change_reason='固化 UI 批量自愈 Skill')
    verify_source(version)
    if not version.is_runnable:
        raise ValidationError('平台自愈 Skill 不可运行')
    return version


def batch_view(binding):
    return {'session_id': binding.session.session_id, 'run': binding.generation_run}


class UiHealingBatchViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def project(self, request):
        from projects.models import Project
        from knowledge_evolution.views import _ensure_project_member
        project = get_object_or_404(Project, pk=request.data.get('project') or request.query_params.get('project'))
        _ensure_project_member(request.user, project.pk)
        if not request.user.has_perms(['ui_automation.view_uitestcase', 'ui_automation.change_uitestcase']):
            raise PermissionDenied('缺少 UI 用例查看或维护权限')
        return project

    def list(self, request):
        project = self.project(request)
        binding = SkillAgentTaskBinding.objects.filter(session__project=project, session__user=request.user,
            generation_mode='ui_healing', session__session_id=request.query_params.get('session_id')).first()
        return Response(batch_view(reconcile_run(binding)) if binding else {'session_id': None, 'run': None})

    def create(self, request):
        from langgraph_integration.models import ChatSession
        project = self.project(request)
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
            raise ValidationError('请选择1至100条 UI 用例')
        ids = list(dict.fromkeys(ids))
        env = get_object_or_404(UiEnvironmentConfig, pk=request.data.get('env_config_id'), project=project)
        if not env.base_url:
            raise ValidationError('环境缺少起始 URL')
        with transaction.atomic():
            cases = list(UiTestCase.objects.select_for_update().filter(pk__in=ids, project=project).order_by('pk'))
            if len(cases) != len(ids):
                raise ValidationError('包含不存在或跨项目用例')
            for other in SkillAgentTaskBinding.objects.filter(session__project=project, generation_run__status__in=ACTIVE):
                other = reconcile_run(other)
                targets = {x['id'] for x in other.generation_context.get('ui_cases', [])}
                targets |= {x.get('ui_test_case_id') for x in other.generation_context.get('functional_cases', [])}
                if other.generation_run.get('status') in ACTIVE and set(ids) & targets:
                    return Response({'detail': '所选 UI 用例已有运行批次，请等待完成后重试'}, status=409)
            by_id = {x.pk: x for x in cases}
            snapshots = []
            for pk in ids:
                case = by_id[pk]
                if case.status == 1:
                    raise ValidationError(f'用例【{case.name}】正在执行，请完成后重试')
                steps = list(case.case_steps.select_related('page_step').order_by('case_sort', 'pk'))
                assertions = assertion_snapshot(pk)
                if not steps or not assertions:
                    raise ValidationError(f'用例【{case.name}】缺少步骤或业务断言，请先完善')
                auth_ids = {x.page_step.auth_state_id for x in steps if x.page_step.auth_state_id}
                from .models import UiAuthState
                if UiAuthState.objects.filter(pk__in=auth_ids).exclude(env_config=env, is_active=True).exists():
                    raise ValidationError(f'用例【{case.name}】的登录态与所选环境不一致')
                snapshots.append({'id': pk, 'name': case.name, 'baseline_assertions': assertions,
                    'auth_state_id': steps[0].page_step.auth_state_id})
            version = healing_version(project, request.user)
            session = ChatSession.objects.create(session_id=uuid.uuid4().hex, user=request.user, project=project,
                title=f'UI 批量自愈 · {len(ids)} 条')
            binding = SkillAgentTaskBinding.objects.create(session=session, source_version=version,
                source_sha256=version.package_sha256, generation_mode='ui_healing',
                generation_context={'ui_cases': snapshots, 'env_config_id': env.pk, 'base_url': env.base_url, 'auth_state_id': None},
                generation_run={'status': 'queued', 'shared_conversation': True, 'items': [{'id': x['id'], 'name': x['name'], 'status': 'queued'} for x in snapshots],
                    'heartbeat_at': timezone.now().isoformat()})
            def dispatch():
                from .tasks import run_ui_healing
                try:
                    task = run_ui_healing.delay(binding.pk)
                    update_run(binding.pk, task_id=str(task.id))
                except Exception:
                    logger.exception('Healing dispatch failed')
                    update_run(binding.pk, status='failed', error='后台任务派发失败，请重新发起')
            transaction.on_commit(dispatch)
        return Response(batch_view(binding), status=202)

    @action(detail=False, methods=['post'])
    def cancel(self, request):
        project = self.project(request)
        binding = get_object_or_404(SkillAgentTaskBinding, session__project=project, session__user=request.user,
            generation_mode='ui_healing', session__session_id=request.data.get('session_id'))
        with transaction.atomic():
            binding = SkillAgentTaskBinding.objects.select_for_update().get(pk=binding.pk)
            if binding.generation_run.get('status') == 'queued':
                items = [{**item, 'status': 'cancelled'} for item in binding.generation_run.get('items', [])]
                update_run(binding.pk, status='cancelled', cancel_requested=True, items=items, finished_at=timezone.now().isoformat())
            elif binding.generation_run.get('status') == 'running':
                update_run(binding.pk, cancel_requested=True)
        return Response({'cancel_requested': True})


def execute_case(binding, case_id):
    """相关 ID 严格配对；不接受历史记录作为本轮执行结果。JWT 仅留在内存。"""
    import websocket
    from rest_framework_simplejwt.tokens import AccessToken
    from orchestrator_integration.builtin_tools.skill_tools import _resolve_skill_runtime_backend_url
    request_id = uuid.uuid4().hex
    base = _resolve_skill_runtime_backend_url().replace('https://', 'wss://').replace('http://', 'ws://')
    token = str(AccessToken.for_user(binding.session.user))
    ws = websocket.create_connection(base + '/ws/ui/web/?background=1&token=' + quote(token), timeout=5)
    started = timezone.now()
    try:
        ws.send(json.dumps({'data': {'func_name': 'u_test_case', 'func_args': {
            'case_id': case_id, 'env_config_id': binding.generation_context['env_config_id'],
            'actuator_id': 'recorder-browser', 'execution_request_id': request_id}}}))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            run = update_run(binding.pk, heartbeat_at=timezone.now().isoformat())
            if run.get('cancel_requested'):
                raise ValueError('批次已取消；已下发的脚本可能仍在收尾')
            try:
                message = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            data = message.get('data') or {}
            result = data.get('func_args') or {}
            if data.get('func_name') not in ('u_case_result', 'case_result') or result.get('execution_request_id') != request_id:
                continue
            record = UiTestCase.objects.get(pk=case_id).execution_records.filter(executor_id=binding.session.user_id,
                start_time__gte=started, environment__execution_request_id=request_id).order_by('-pk').first()
            if not record or str((record.environment or {}).get('env_config_id')) != str(binding.generation_context['env_config_id']):
                raise ValueError('缺少本轮执行记录或执行环境不一致')
            return record
        raise ValueError('脚本执行超时，请检查执行记录和录制器状态')
    finally:
        ws.close()


def process_batch(pk):
    from orchestrator_integration.ui_generation_browser import run_ui_browser
    with transaction.atomic():
        binding = SkillAgentTaskBinding.objects.select_for_update().select_related('session__user', 'source_version').get(pk=pk)
        if binding.generation_run.get('status') != 'queued': return
        update_run(pk, status='running', started_at=timezone.now().isoformat(), heartbeat_at=timezone.now().isoformat())
    items = binding.generation_run['items']
    for index, snapshot in enumerate(binding.generation_context['ui_cases']):
        if update_run(pk).get('cancel_requested'): break
        item = items[index]
        item['status'] = 'running'
        update_run(pk, items=items, active_source_id=snapshot['id'], active_session_id=binding.session.session_id)
        try:
            from knowledge_evolution.views import _ensure_project_member
            _ensure_project_member(binding.session.user, binding.session.project_id)
            if not binding.session.user.has_perms(['ui_automation.view_uitestcase', 'ui_automation.change_uitestcase']):
                raise ValueError('当前用户已无自愈权限')
            env = binding.generation_context
            if not UiEnvironmentConfig.objects.filter(pk=env['env_config_id'], project_id=binding.session.project_id, base_url=env['base_url']).exists():
                raise ValueError('环境配置已变化，请重新确认')
            if assertion_snapshot(snapshot['id']) != snapshot['baseline_assertions']:
                raise ValueError('业务断言已变化，请人工复核')
            record = execute_case(binding, snapshot['id'])
            item['initial_record_id'] = record.pk
            if record.status == 2:
                item.update(status='passed', message='脚本首跑通过，无需自愈')
            elif record.status == 3 and record.step_results:
                item.update(status='healing', message='脚本失败，AI 正在诊断与修复')
                binding.generation_context['auth_state_id'] = snapshot['auth_state_id']
                binding.session.auth_state_id = snapshot['auth_state_id']
                binding.session.save(update_fields=['auth_state'])
                binding.save(update_fields=['generation_context'])
                update_run(pk, items=items)
                async_to_sync(consume)(binding, f'当前修复 UI 用例 {snapshot["id"]}：{snapshot["name"]}。先读取 get_ui_healing_context，按锁定 Skill 修复一次。平台随后重跑，禁止自行执行或宣称通过。', parent_id=pk)
                if update_run(pk).get('cancel_requested'): raise ValueError('批次已取消')
                if assertion_snapshot(snapshot['id']) != snapshot['baseline_assertions']:
                    raise ValueError('业务断言发生变化，禁止判定自愈成功，请人工复核')
                item.update(status='verifying', message='修复后重跑验证')
                update_run(pk, items=items)
                retry = execute_case(binding, snapshot['id'])
                item['retry_record_id'] = retry.pk
                if assertion_snapshot(snapshot['id']) != snapshot['baseline_assertions']:
                    raise ValueError('重跑期间业务断言发生变化，请人工复核')
                item.update(status='healed' if retry.status == 2 else 'failed',
                    message='自愈后重跑通过' if retry.status == 2 else (retry.error_message or '重跑仍失败，需人工处理'))
            else:
                item.update(status='failed', message=record.error_message or '首跑未正常完成，未启动 AI 修复')
        except (Exception, asyncio.CancelledError) as exc:
            logger.exception('UI healing failed case=%s', snapshot['id'])
            item.update(status='failed', message=str(exc)[:500] if isinstance(exc, ValueError) else '执行异常，请查看批次会话及服务日志')
        finally:
            try: run_ui_browser(binding, 'close', [])
            except Exception: pass
        update_run(pk, items=items)
    cancelled = update_run(pk).get('cancel_requested')
    for item in items:
        if item['status'] == 'queued': item['status'] = 'cancelled'
    update_run(pk, items=items, active_source_id=None, active_session_id=None, finished_at=timezone.now().isoformat(),
        status='cancelled' if cancelled else ('completed' if all(x['status'] in ('passed', 'healed') for x in items) else 'partial_failure'))
