from django.core.exceptions import ValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from projects.models import Project
from skills.models import Skill, SkillVersion
from .hub_assessment_models import SkillAssessmentSession
from . import hub_assessment as service


class SkillAssessmentViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def project(self, request):
        from .views import _ensure_project_member
        project = get_object_or_404(Project, pk=request.query_params.get('project') or request.data.get('project'))
        _ensure_project_member(request.user, project.pk)
        return project

    def scoped(self, request):
        return SkillAssessmentSession.objects.filter(project=self.project(request)).select_related('source_version__skill')

    def list(self, request):
        items = self.scoped(request)
        if request.query_params.get('skill_id'):
            skill = get_object_or_404(Skill, pk=request.query_params['skill_id'])
            items = items.filter(source_version__skill__name=skill.name)
        return Response({'items': [{'id': str(item.pk), 'source_version': item.source_version.version,
                                  'state': item.state, 'mode': item.mode, 'created_at': item.created_at.isoformat()}
                                 for item in items[:30]]})

    def retrieve(self, request, pk=None):
        return Response(service.session_view(get_object_or_404(self.scoped(request), pk=pk)))

    @staticmethod
    def perform(callback):
        try:
            return Response(service.session_view(callback()))
        except ValidationError as exc:
            return Response({'detail': '；'.join(exc.messages)}, status=400)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('独立 Skill 测评失败')
            return Response({'detail': '执行失败，详细原因已记录在测评会话中'}, status=400)

    def create(self, request):
        if request.data.get('mode') == 'external':
            return Response({'detail': '真实产出复核请前往 Agent 任务；此处不接收手工产出证据'}, status=400)
        project = self.project(request)
        version = get_object_or_404(SkillVersion.objects.select_related('skill', 'release'), pk=request.data.get('source_version_id'))
        return self.perform(lambda: service.create_session(
            project=project, version=version, mode=request.data.get('mode', 'structure'),
            cases=request.data.get('cases', []), actor=request.user, policy=request.data.get('policy'),
            confirmed=request.data.get('confirmed', False),
        ))

    def enqueue(self, request, pk, action, payload):
        import uuid
        from django.db import transaction
        from .tasks import hub_assessment_job
        project = self.project(request)
        with transaction.atomic():
            session = get_object_or_404(SkillAssessmentSession.objects.select_for_update(), pk=pk, project=project)
            if session.mode == 'external':
                return Response({'detail': '此历史测评仅可查看，真实产出复核请前往 Agent 任务'}, status=400)
            if session.state in {'queued', 'running', 'submitted'}:
                return Response({'detail': '会话正在执行或已提交'}, status=400)
            session.job_id = str(uuid.uuid4())
            session.state, session.error = 'queued', ''
            session.save(update_fields=['job_id', 'state', 'error', 'updated_at'])
        try:
            hub_assessment_job.apply_async(args=[str(session.pk), action, payload, request.user.pk, session.job_id], task_id=session.job_id)
        except Exception:
            session.state, session.error = 'failed', '任务队列不可用，请稍后重试'
            session.save(update_fields=['state', 'error', 'updated_at'])
            return Response({'detail': session.error}, status=503)
        return Response(service.session_view(session), status=202)

    @action(detail=True, methods=['post'], url_path='assess')
    def assess(self, request, pk=None):
        return self.enqueue(request, pk, 'assess', {
            'candidate_id': request.data.get('candidate_id', ''),
            'evidence': request.data.get('external_outputs'), 'confirmed': request.data.get('confirmed', False),
            'version_sha256': request.data.get('package_sha256', ''),
        })

    @action(detail=True, methods=['post'], url_path='generate')
    def generate(self, request, pk=None):
        return self.enqueue(request, pk, 'generate', {})

    @action(detail=True, methods=['post'], url_path='select')
    def select(self, request, pk=None):
        session = get_object_or_404(self.scoped(request), pk=pk)
        if session.mode == 'external':
            return Response({'detail': '真实产出复核已移至 Agent 任务'}, status=400)
        return self.perform(lambda: service.select_candidate(
            session.pk, candidate_id=request.data.get('candidate_id', ''), reason=str(request.data.get('reason') or ''),
        ))

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        from .views import _ensure_test_lead
        session = get_object_or_404(self.scoped(request), pk=pk)
        _ensure_test_lead(request.user, session.project_id)
        if session.mode == 'external':
            return Response({'detail': '真实产出复核已移至 Agent 任务，历史手工证据不能提交'}, status=400)
        return self.perform(lambda: service.submit_candidate(session.pk, actor=request.user, confirmed=request.data.get('confirmed', False)))
