"""Hub 到 Agent 的任务交接：会话、精确版本与包指纹绑定。"""
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import viewsets
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
            'version': version.version, 'package_sha256': binding.source_sha256}


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
        return Response({'binding': binding_view(binding) if binding else None})

    def create(self, request):
        project = self.project(request)
        version = get_object_or_404(SkillVersion.objects.select_related('skill', 'release'), pk=request.data.get('source_version_id'))
        generation_mode = str(request.data.get('generation_mode') or '')
        if generation_mode not in {'', 'interface_case', 'scenario'}:
            return Response({'detail': '不支持的生成模式'}, status=400)
        try:
            verify_source(version)
            if not version.is_runnable:
                raise ValidationError('此版本当前不可运行')
            with transaction.atomic():
                session = ChatSession.objects.create(session_id=uuid.uuid4().hex, user=request.user, project=project,
                    title=f'Skill 任务 · {version.skill.name} @ {version.version}'[:200])
                binding = SkillAgentTaskBinding.objects.create(session=session, source_version=version, source_sha256=version.package_sha256, generation_mode=generation_mode)
            return Response(binding_view(binding), status=201)
        except ValidationError as exc:
            return Response({'detail': '；'.join(exc.messages)}, status=400)
