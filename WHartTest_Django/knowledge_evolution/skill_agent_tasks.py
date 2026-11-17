"""Hub 到 Agent 的任务交接：会话、精确版本与包指纹绑定。"""
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import get_object_or_404
from langgraph_integration.models import ChatSession
from projects.models import Project
from skills.models import SkillVersion
from .hub_assessment import verify_source
from .hub_assessment_models import SkillAgentTaskBinding


def binding_view(binding):
    version = binding.source_version
    return {'generation_mode': binding.generation_mode, 'session_id': binding.session.session_id, 'project_id': binding.session.project_id,
            'skill_version_id': str(version.pk), 'skill_name': version.skill.name,
            'version': version.version, 'package_sha256': binding.source_sha256, 'run': binding.generation_run}


def resolve_agent_task_version(*, session_id, project_id, actor, requested_version_id=''):
    """每次执行均读服务端绑定，刷新或从历史恢复也不能丢失版本。"""
    binding = SkillAgentTaskBinding.objects.select_related('session', 'source_version__skill').filter(session__session_id=session_id).first()
    if binding is None:
        return requested_version_id
    if binding.session.user_id != actor.pk or binding.session.project_id != int(project_id):
        raise ValidationError('Agent 任务不存在或不属于当前用户和项目')
    version = binding.source_version
    if requested_version_id and str(version.pk) != requested_version_id:
        raise ValidationError('Agent 任务已绑定精确 Skill 版本，不能更换')
    verify_source(version)
    if version.package_sha256 != binding.source_sha256:
        raise ValidationError('Agent 任务绑定的 Skill 包指纹已变化')
    return str(version.pk)


def builtin_ui_version(project, actor):
    from ui_automation.case_conversion import local_skill
    from skills.versions import SkillVersionService
    builtin = local_skill()
    from pathlib import Path
    import hashlib
    digest = hashlib.sha256(builtin['sha256'].encode())
    for path in sorted(Path(builtin['directory']).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.name != '.DS_Store':
            digest.update(path.relative_to(builtin['directory']).as_posix().encode())
            digest.update(path.read_bytes())
    builtin['sha256'] = digest.hexdigest()
    version = SkillVersion.objects.select_related('skill', 'release').filter(
        skill__name='excel-to-ui-case', source_metadata__builtin_sha256=builtin['sha256'],
    ).order_by('-created_at').first()
    if version is None:
        _, version = SkillVersionService.create_candidate_from_dir(
            source_dir=builtin['directory'], project=project, actor=actor,
            version_override='0.0.0-builtin.' + builtin['sha256'][:12],
            source_metadata={'platform_builtin': 'excel-to-ui-case', 'builtin_sha256': builtin['sha256']},
            change_reason='固化平台内置业务用例探索转换 Skill',
        )
    verify_source(version)
    if not version.is_runnable:
        raise ValidationError('平台内置转换 Skill 版本不可运行，请联系管理员')
    return version


class SkillAgentTaskViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def project(self, request):
        from .views import _ensure_project_member
        project = get_object_or_404(Project, pk=request.query_params.get('project') or request.data.get('project'))
        _ensure_project_member(request.user, project.pk)
        return project

    def list(self, request):
        project = self.project(request)
        binding = SkillAgentTaskBinding.objects.select_related('session', 'source_version__skill').filter(
            session__project=project, session__user=request.user,
            session__session_id=request.query_params.get('session_id', ''),
        ).first()
        if binding:
            from .ui_generation_jobs import reconcile_run
            binding = reconcile_run(binding)
        return Response({'binding': binding_view(binding) if binding else None})

    @action(detail=False, methods=['post'])
    def start_conversion(self, request):
        project = self.project(request)
        with transaction.atomic():
            binding = get_object_or_404(SkillAgentTaskBinding.objects.select_for_update(),
                session__session_id=request.data.get('session_id'), session__project=project,
                session__user=request.user, generation_mode='ui_case')
            verify_source(binding.source_version)
            if not request.user.has_perms(['ui_automation.add_uitestcase', 'ui_automation.change_uitestcase', 'testcases.change_testcase']):
                return Response({'detail': '缺少探索转换权限'}, status=403)
            if not binding.generation_run:
                from testcases.models import TestCase
                ids = [x['id'] for x in binding.generation_context['functional_cases']]
                list(TestCase.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
                for other in SkillAgentTaskBinding.objects.filter(session__project=project, generation_run__status__in=['queued','running']).exclude(pk=binding.pk):
                    from .ui_generation_jobs import reconcile_run
                    other = reconcile_run(other)
                    if other.generation_run.get('status') not in ('queued', 'running'):
                        continue
                    if set(ids) & {x['id'] for x in other.generation_context.get('functional_cases', [])}:
                        return Response({'detail': '所选业务用例已有探索批次，请查看该批次进度或取消后重试', 'existing_session_id': other.session.session_id}, status=409)
                from .ui_generation_jobs import run_ui_conversion
                binding.generation_run = {'status': 'queued', 'items': [], 'cancel_requested': False}
                binding.save(update_fields=['generation_run'])
                def dispatch():
                    try:
                        task = run_ui_conversion.delay(binding.pk)
                        from .ui_generation_jobs import update_run
                        update_run(binding.pk, task_id=str(task.id))
                    except Exception:
                        from .ui_generation_jobs import update_run
                        update_run(binding.pk, status='failed', error='后台任务派发失败，请重新发起')
                transaction.on_commit(dispatch)
        return Response(binding_view(binding), status=202)

    @action(detail=False, methods=['post'])
    def cancel_conversion(self, request):
        project = self.project(request)
        from .ui_generation_jobs import update_run
        binding = get_object_or_404(SkillAgentTaskBinding, session__session_id=request.data.get('session_id'),
            session__project=project, session__user=request.user, generation_mode='ui_case')
        run = update_run(binding.pk, cancel_requested=True)
        if run.get('active_session_id'):
            from orchestrator_integration.stop_signal import set_stop_signal
            set_stop_signal(run['active_session_id'])
        return Response({'cancel_requested': True})

    def create(self, request):
        project = self.project(request)
        generation_mode = str(request.data.get('generation_mode') or '')
        if generation_mode not in {'', 'interface_case', 'scenario', 'ui_case'}:
            return Response({'detail': '不支持的生成模式'}, status=400)
        try:
            version = builtin_ui_version(project, request.user) if generation_mode == 'ui_case' else get_object_or_404(SkillVersion.objects.select_related('skill', 'release'), pk=request.data.get('source_version_id'))
            context = {}
            if generation_mode == 'ui_case':
                from orchestrator_integration.ui_generation_runtime import validate_ui_context
                context = validate_ui_context(project, request.user, request.data.get('generation_context'))
            verify_source(version)
            if not version.is_runnable:
                raise ValidationError('此版本当前不可运行')
            with transaction.atomic():
                session = ChatSession.objects.create(session_id=uuid.uuid4().hex, user=request.user, project=project,
                    title=(f'业务用例转 UI · {len(context.get("functional_cases", []))} 条' if generation_mode == 'ui_case' else f'Skill 任务 · {version.skill.name} @ {version.version}')[:200])
                if generation_mode == 'ui_case' and context.get('auth_state_id'):
                    session.auth_state_id = context['auth_state_id']
                    session.save(update_fields=['auth_state'])
                binding = SkillAgentTaskBinding.objects.create(session=session, source_version=version, source_sha256=version.package_sha256, generation_mode=generation_mode, generation_context=context)
            return Response(binding_view(binding), status=201)
        except ValidationError as exc:
            return Response({'detail': '；'.join(exc.messages)}, status=400)
