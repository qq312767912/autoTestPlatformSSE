from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
from celery import current_app

from projects.models import ProjectMember
from .models import AnalysisTask, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential
from .serializers import AnalysisTaskSerializer, CredentialSerializer, GitLabConnectionSerializer, ProjectRepositorySerializer, TestRequirementDraftSerializer
from .services import GitLabClient


def _can_access(user, project_id):
    return user.is_superuser or ProjectMember.objects.filter(project_id=project_id, user=user).exists()


class GitLabConnectionViewSet(viewsets.ModelViewSet):
    queryset = GitLabConnection.objects.all()
    serializer_class = GitLabConnectionSerializer
    permission_classes = [IsAuthenticated]
    def _admin(self):
        if not self.request.user.is_superuser: raise PermissionDenied("仅系统管理员可维护GitLab连接")
    def perform_create(self, serializer): self._admin(); serializer.save()
    def perform_update(self, serializer): self._admin(); serializer.save()
    def perform_destroy(self, instance): self._admin(); instance.delete()


class ProjectRepositoryViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectRepositorySerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = ProjectRepository.objects.select_related("project", "connection")
        if not self.request.user.is_superuser: qs = qs.filter(project__members__user=self.request.user)
        project_id = self.request.query_params.get("project")
        return qs.filter(project_id=project_id) if project_id else qs
    def perform_create(self, serializer):
        if not _can_access(self.request.user, self.request.data.get("project")): raise PermissionDenied()
        serializer.save()
    @action(detail=True, methods=["get"], url_path="merge-requests")
    def merge_requests(self, request, pk=None):
        repo = self.get_object()
        credential = UserGitLabCredential.objects.get(project=repo.project, connection=repo.connection, user=request.user)
        data = GitLabClient(repo.connection, credential.get_token()).merge_requests(repo.gitlab_project_id)
        return Response(data)


class CredentialViewSet(viewsets.ModelViewSet):
    serializer_class = CredentialSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = UserGitLabCredential.objects.filter(user=self.request.user)
        project_id = self.request.query_params.get("project")
        return qs.filter(project_id=project_id) if project_id else qs
    def perform_create(self, serializer):
        if not _can_access(self.request.user, self.request.data.get("project")): raise PermissionDenied()
        serializer.save()
    @action(detail=False, methods=["post"], url_path="test")
    def test_connection(self, request):
        project_id, connection_id, token = request.data.get("project"), request.data.get("connection"), request.data.get("token")
        if not _can_access(request.user, project_id): raise PermissionDenied()
        connection = GitLabConnection.objects.get(pk=connection_id)
        if not token:
            token = UserGitLabCredential.objects.get(project_id=project_id, connection=connection, user=request.user).get_token()
        version = GitLabClient(connection, token).get("/version")
        return Response({"success": True, "version": version})


class AnalysisTaskViewSet(viewsets.ModelViewSet):
    serializer_class = AnalysisTaskSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = AnalysisTask.objects.select_related("project", "repository", "creator").prefetch_related("test_requirement_drafts")
        if not self.request.user.is_superuser: qs = qs.filter(project__members__user=self.request.user)
        project_id = self.request.query_params.get("project")
        return qs.filter(project_id=project_id) if project_id else qs
    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not _can_access(self.request.user, project.id): raise PermissionDenied()
        serializer.save(creator=self.request.user)
    def destroy(self, request, *args, **kwargs):
        task = self.get_object()
        membership = ProjectMember.objects.filter(project=task.project, user=request.user).first()
        if not (request.user.is_superuser or task.creator_id == request.user.id or membership and membership.role in {"owner", "admin"}):
            raise PermissionDenied("无权删除该分析任务")
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        task = self.get_object()
        if task.creator_id != request.user.id and not request.user.is_superuser: raise PermissionDenied("只能使用自己的GitLab Token执行分析")
        if task.status not in {"pending", "failed", "partial", "cancelled"}:
            return Response({"detail": "分析任务正在执行或已经完成"}, status=status.HTTP_409_CONFLICT)
        from .tasks import run_code_analysis
        task.status, task.progress, task.current_step, task.error_message = "pending", 0, "等待后台执行", ""
        task.save(update_fields=["status", "progress", "current_step", "error_message", "updated_at"])
        async_result = run_code_analysis.delay(str(task.id))
        task.celery_task_id = async_result.id
        task.save(update_fields=["celery_task_id", "updated_at"])
        return Response(self.get_serializer(task).data, status=status.HTTP_202_ACCEPTED)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        task = self.get_object()
        if task.status in {"completed", "failed", "cancelled"}:
            return Response(self.get_serializer(task).data)
        task.status = "cancelled"; task.current_step = "用户已取消"; task.save(update_fields=["status", "current_step", "updated_at"])
        if task.celery_task_id:
            current_app.control.revoke(task.celery_task_id, terminate=False)
        return Response(self.get_serializer(task).data)

    def _markdown_response(self, task, report_type):
        if report_type == "change":
            report = task.change_report or {}
            summary = report.get("summary", {})
            lines = [
                f"# {task.title or task.repository.name} - 代码审查报告", "",
                "## 分析输入", "",
                f"- 平台项目：{task.project.name}",
                f"- 代码仓库：{task.repository.path_with_namespace}",
                f"- 分析范围：{task.base_sha} → {task.head_sha}",
                f"- 分析模式：{task.get_mode_display()}",
                f"- 完成时间：{task.completed_at or '-'}", "",
                "## 概览", "",
                f"- 变更文件：{summary.get('changed_files', 0)}",
                f"- 新增行：{summary.get('additions', 0)}",
                f"- 删除行：{summary.get('deletions', 0)}",
                f"- 总变更行：{summary.get('changed_lines', 0)}",
                f"- 风险总数：{summary.get('risk_count', 0)}",
                f"- 高风险：{summary.get('high_risk_count', 0)}",
                f"- 机器覆盖率：{task.machine_coverage}%",
                f"- AI覆盖率：{task.ai_coverage}%",
                f"- Token消耗：{task.token_usage}", "",
                "## 风险与影响", "",
            ]
            for index, item in enumerate(report.get("findings", []), 1):
                lines.extend([
                    f"### {index}. [{item.get('severity', 'unknown').upper()}] {item.get('change', '')}", "",
                    f"- 文件：`{item.get('file', '')}`",
                    f"- 来源：{item.get('source', '')}",
                    f"- 置信度：{item.get('confidence', '-')}",
                    f"- 影响：{item.get('impact', '')}", "",
                    "```", str(item.get("evidence", "")), "```", "",
                ])
            lines.extend(["## 变更文件", ""])
            lines.extend(f"- `{item.get('path', '')}`" for item in report.get("files", []))
        else:
            report = task.test_report or {}
            summary = report.get("summary", {})
            lines = [
                f"# {task.title or task.repository.name} - 测试分析报告", "",
                f"- 代码仓库：{task.repository.path_with_namespace}",
                f"- 分析范围：{task.base_sha} → {task.head_sha}",
                f"- 测试需求点：{summary.get('test_point_count', 0)}",
                f"- 高优先级：{summary.get('high_priority_count', 0)}", "",
                "## 测试需求点", "",
            ]
            for index, item in enumerate(report.get("test_requirements", []), 1):
                lines.extend([
                    f"### {index}. {item.get('title', '')}", "",
                    f"- 优先级：{item.get('priority', '')}",
                    f"- 类型：{item.get('test_type', '')}",
                    f"- 测试目标：{item.get('objective', '')}",
                    f"- 预期结果：{item.get('expected_result', '')}",
                    f"- 来源：{item.get('source_finding_key', '')}", "",
                ])
            lines.extend(["## 回归建议", ""])
            lines.extend(f"- {item}" for item in report.get("regression_suggestions", []))
            lines.extend(["", "## 覆盖缺口", ""])
            lines.extend(f"- {item}" for item in report.get("coverage_gaps", []))
        filename = f"code-analysis-{task.id}-{report_type}.txt"
        response = HttpResponse("\ufeff" + "\n".join(lines), content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=["get"], url_path="download-change-report")
    def download_change_report(self, request, pk=None):
        return self._markdown_response(self.get_object(), "change")

    @action(detail=True, methods=["get"], url_path="download-test-report")
    def download_test_report(self, request, pk=None):
        return self._markdown_response(self.get_object(), "test")


class TestRequirementDraftViewSet(viewsets.ModelViewSet):
    serializer_class = TestRequirementDraftSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        qs = TestRequirementDraft.objects.select_related("task__project")
        if not self.request.user.is_superuser: qs = qs.filter(task__project__members__user=self.request.user)
        return qs
