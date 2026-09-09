from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
from celery import current_app
from django.db import transaction
from django.db.models import Q

from projects.models import ProjectMember
from .models import AnalysisTask, AnalysisTaskExecutionLog, GitLabConnection, ProjectRepository, TestRequirementDraft, UserGitLabCredential
from .serializers import AnalysisTaskExecutionLogSerializer, AnalysisTaskSerializer, CredentialSerializer, GitLabConnectionSerializer, ProjectRepositorySerializer, TestRequirementDraftSerializer
from .services import GitLabClient, LocalGitClient, generate_suggested_patch, normalize_test_point_for_display, remove_ocr_repositories_for_repository, remove_ocr_repository


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
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            qs = qs.filter(project__members__user=self.request.user)
        project_id = self.request.query_params.get("project")
        return qs.filter(project_id=project_id) if project_id else qs
    def perform_create(self, serializer):
        if not _can_access(self.request.user, self.request.data.get("project")): raise PermissionDenied()
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        repo = self.get_object()
        if not _can_access(request.user, repo.project_id):
            raise PermissionDenied("无权删除该代码仓库")
        task_count = repo.analysis_tasks.count()
        if task_count:
            return Response(
                {
                    "detail": f"该仓库仍有 {task_count} 条审查记录，请先删除全部审查记录",
                    "analysis_task_count": task_count,
                },
                status=status.HTTP_409_CONFLICT,
            )
        remove_ocr_repositories_for_repository(repo.id)
        repo.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _gitlab_client(self, repo, user):
        try:
            credential = UserGitLabCredential.objects.get(
                project=repo.project, connection=repo.connection, user=user,
            )
        except UserGitLabCredential.DoesNotExist:
            raise PermissionDenied("请先配置当前用户的 GitLab Token")
        return GitLabClient(repo.connection, credential.get_token())

    @action(detail=True, methods=["get"], url_path="commits")
    def commits(self, request, pk=None):
        repo = self.get_object()
        try:
            if repo.source_type == "local_git":
                data = LocalGitClient(repo.local_path).commits(limit=40)
            else:
                data = self._gitlab_client(repo, request.user).commits(
                    repo.gitlab_project_id, repo.default_branch, limit=40,
                )
            return Response(data)
        except PermissionDenied:
            raise
        except Exception as exc:
            return Response(
                {"detail": f"读取最近提交失败：{exc}"}, status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="validate-refs")
    def validate_refs(self, request, pk=None):
        repo = self.get_object()
        base_ref = str(request.data.get("base_sha") or "").strip()
        head_ref = str(request.data.get("head_sha") or "").strip()
        if not base_ref or not head_ref:
            return Response(
                {"detail": "请选择或输入基准 Commit 和目标 Commit"}, status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if repo.source_type == "local_git":
                client = LocalGitClient(repo.local_path)
                base_sha, head_sha = client.resolve(base_ref), client.resolve(head_ref)
            else:
                client = self._gitlab_client(repo, request.user)
                base_sha = client.commit(repo.gitlab_project_id, base_ref)["id"]
                head_sha = client.commit(repo.gitlab_project_id, head_ref)["id"]
        except PermissionDenied:
            raise
        except Exception as exc:
            return Response(
                {"detail": f"Commit 校验失败，请确认提交存在且当前用户有权访问：{exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if base_sha == head_sha:
            return Response(
                {"detail": "基准 Commit 和目标 Commit 不能相同"}, status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"valid": True, "base_sha": base_sha, "head_sha": head_sha})

    @action(detail=True, methods=["get"], url_path="merge-requests")
    def merge_requests(self, request, pk=None):
        repo = self.get_object()
        if repo.source_type != "gitlab":
            return Response({"detail": "本地 Git 仓库不支持读取 Merge Request"}, status=status.HTTP_400_BAD_REQUEST)
        data = self._gitlab_client(repo, request.user).merge_requests(repo.gitlab_project_id)
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
        qs = AnalysisTask.objects.select_related("project", "repository", "creator", "executor").prefetch_related("test_requirement_drafts")
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            qs = qs.filter(project__members__user=self.request.user)
        project_id = self.request.query_params.get("project")
        return qs.filter(project_id=project_id) if project_id else qs
    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not _can_access(self.request.user, project.id): raise PermissionDenied()
        task = serializer.save(creator=self.request.user)
        AnalysisTaskExecutionLog.objects.create(task=task, event="created", actor=self.request.user, message="已创建代码审查任务")
    def destroy(self, request, *args, **kwargs):
        task = self.get_object()
        membership = ProjectMember.objects.filter(project=task.project, user=request.user).first()
        if not (request.user.is_superuser or task.creator_id == request.user.id or membership and membership.role in {"owner", "admin"}):
            raise PermissionDenied("无权删除该分析任务")
        task_id = task.pk
        task.delete()
        remove_ocr_repository(task_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        force_refresh = request.data.get("force_refresh", True)
        if not isinstance(force_refresh, bool):
            return Response({"detail": "force_refresh 必须为布尔值"}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            task = AnalysisTask.objects.select_for_update().get(pk=pk)
            if not _can_access(request.user, task.project_id): raise PermissionDenied("仅项目成员可执行分析")
            if task.status not in {"pending", "completed", "failed", "partial", "cancelled"}:
                return Response({"detail": "分析任务正在执行，请完成或取消后再重跑"}, status=status.HTTP_409_CONFLICT)
            running = AnalysisTask.objects.filter(repository=task.repository).filter(
                Q(status__in={"fetching", "machine_analyzing", "ai_analyzing", "generating_tests"}) |
                (Q(status="pending") & ~Q(celery_task_id=""))
            ).exclude(pk=task.pk).exists()
            if running:
                return Response({"detail": "该仓库已有审查任务正在运行，请完成或取消后再发起"}, status=status.HTTP_409_CONFLICT)
            retrying = task.status in {"completed", "failed", "partial", "cancelled"}
            removed = task.test_requirement_drafts.filter(status="draft").delete()[0]
            task.status, task.progress, task.current_step, task.error_message, task.executor = "pending", 0, "等待后台执行", "", request.user
            task.save(update_fields=["status", "progress", "current_step", "error_message", "executor", "updated_at"])
            AnalysisTaskExecutionLog.objects.create(task=task, event="queued", actor=request.user, message="重新提交审查任务" if retrying else "审查任务已进入队列", detail={"cleared_drafts": removed})
            from .tasks import run_code_analysis
            async_result = run_code_analysis.delay(str(task.id), force_refresh=force_refresh)
            task.celery_task_id = async_result.id
            task.save(update_fields=["celery_task_id", "updated_at"])
        return Response(self.get_serializer(task).data, status=status.HTTP_202_ACCEPTED)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        task = self.get_object()
        if task.status in {"completed", "failed", "cancelled"}:
            return Response(self.get_serializer(task).data)
        task.status = "cancelled"; task.current_step = "用户已取消"; task.save(update_fields=["status", "current_step", "updated_at"])
        AnalysisTaskExecutionLog.objects.create(task=task, event="cancelled", actor=request.user, message="用户取消分析任务")
        if task.celery_task_id:
            current_app.control.revoke(task.celery_task_id, terminate=False)
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["get"], url_path="execution-logs")
    def execution_logs(self, request, pk=None):
        task = self.get_object()
        return Response(AnalysisTaskExecutionLogSerializer(task.execution_logs.select_related("actor"), many=True).data)

    @action(detail=True, methods=["get"], url_path="diff")
    def diff(self, request, pk=None):
        """读取此次任务已保存的 Diff；不会再次访问外部 Git 服务。"""
        task = self.get_object()
        file_path = request.query_params.get("file", "")
        raw_diff = task.raw_diff or ""
        if file_path:
            marker = f"diff -- {file_path}\n"
            start = raw_diff.find(marker)
            if start < 0:
                return Response({"detail": "未找到该文件的 Diff"}, status=status.HTTP_404_NOT_FOUND)
            next_start = raw_diff.find("\ndiff -- ", start + len(marker))
            raw_diff = raw_diff[start:next_start if next_start >= 0 else None]
        return Response({"file": file_path, "base_sha": task.base_sha, "head_sha": task.head_sha, "diff": raw_diff})

    @action(detail=True, methods=["post"], url_path="suggested-patch")
    def suggested_patch(self, request, pk=None):
        """按需生成单条修复建议；仅校验补丁，不修改被审查仓库。"""
        task = self.get_object()
        if task.status not in {"completed", "partial"}:
            return Response({"detail": "报告完成后才能生成建议修复"}, status=status.HTTP_409_CONFLICT)
        finding_key = str(request.data.get("finding_key") or "")
        findings = (task.change_report or {}).get("findings") or []
        finding = next((item for item in findings if str(item.get("key")) == finding_key), None)
        if not finding:
            return Response({"detail": "未找到对应风险点"}, status=status.HTTP_404_NOT_FOUND)
        if finding.get("patch_status"):
            return Response(finding)

        review_client = None
        if task.repository.source_type != "local_git":
            try:
                credential = UserGitLabCredential.objects.get(
                    project=task.project, connection=task.repository.connection, user=request.user,
                )
            except UserGitLabCredential.DoesNotExist:
                return Response({"detail": "请先配置当前用户的 GitLab Token"}, status=status.HTTP_400_BAD_REQUEST)
            review_client = GitLabClient(task.repository.connection, credential.get_token())
        generated, tokens, note = generate_suggested_patch(task, finding, review_client)

        with transaction.atomic():
            locked = AnalysisTask.objects.select_for_update().get(pk=task.pk)
            report = dict(locked.change_report or {})
            stored_findings = list(report.get("findings") or [])
            for index, item in enumerate(stored_findings):
                if str(item.get("key")) == finding_key:
                    stored_findings[index] = generated
                    break
            report["findings"] = stored_findings
            locked.change_report = report
            locked.token_usage += tokens
            locked.save(update_fields=["change_report", "token_usage", "updated_at"])
        return Response({**generated, "generation_note": note})

    def _markdown_response(self, task, report_type):
        if report_type == "change":
            report = task.change_report or {}
            summary = report.get("summary", {})
            ocr_status = report.get("ocr_status") or {}
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
                f"- OCR状态：{ocr_status.get('message') or '历史报告未记录 OCR 状态'}",
                f"- Token消耗：{task.token_usage}", "",
                "## 风险与影响", "",
            ]
            for index, item in enumerate(report.get("findings", []), 1):
                lines.extend([
                    f"### {index}. [{item.get('severity', 'unknown').upper()}] {item.get('change', '')}", "",
                    f"- 文件：`{item.get('file', '')}`",
                    f"- 来源：{item.get('source', '')}",
                    f"- 置信度：{item.get('confidence', '-')}",
                    "- 风险凭据：", "",
                    "```", str(item.get("evidence", "")), "```", "",
                    f"- 影响：{item.get('impact', '')}",
                    f"- 建议：{item.get('recommendation') or '覆盖相关正常流程、异常分支及调用链后再决定是否修复'}", "",
                    f"- 建议修复状态：{'可应用' if item.get('patch_status') == 'applicable' else '仅供参考'}", "",
                ])
                if item.get("suggested_patch"):
                    lines.extend(["```diff", str(item["suggested_patch"]), "```", ""])
            lines.extend(["## 变更文件", ""])
            lines.extend(f"- `{item.get('path', '')}`" for item in report.get("files", []))
        else:
            report = task.test_report or {}
            summary = report.get("summary", {})
            iteration_summary = report.get("iteration_summary") or {}
            ignored_sources = set(task.test_requirement_drafts.filter(status="ignored").values_list("source_finding_key", flat=True))
            test_requirements = [
                normalize_test_point_for_display(item, (task.change_report or {}).get("findings", []))
                for item in report.get("test_requirements", []) if item.get("source_finding_key") not in ignored_sources
            ]
            risk_test_requirements = [
                item for item in test_requirements
                if item.get("change_group") in {"风险排查", "风险点", "风险回归"}
            ]
            priority_order = {"high": 0, "medium": 1, "low": 2}
            risk_test_requirements.sort(key=lambda item: priority_order.get(item.get("priority"), 9))
            iteration_test_requirements = [item for item in test_requirements if item not in risk_test_requirements]
            lines = [
                f"# {task.title or task.repository.name} - 测试分析报告", "",
                f"- 代码仓库：{task.repository.path_with_namespace}",
                f"- 分析范围：{task.base_sha} → {task.head_sha}",
                f"- 迭代验证：{len(iteration_test_requirements)}",
                f"- 风险排查：{len(risk_test_requirements)}",
                f"- 高优先级：{summary.get('high_priority_count', 0)}", "",
            ]
            if iteration_summary.get("title"):
                change_groups = iteration_summary.get("change_groups") or []
                summary_points = iteration_summary.get("summary_points") or [
                    f"{item.get('name')}：{item.get('description')}"
                    for item in change_groups
                    if item.get("name") and item.get("description")
                ] or iteration_summary.get("change_items", []) or ([iteration_summary.get("description")] if iteration_summary.get("description") else [])
                if len(summary_points) == len(change_groups):
                    scale_order = {"large": 3, "medium": 2, "small": 1}
                    ranked_indexes = sorted(
                        range(len(change_groups)),
                        key=lambda index: (
                            scale_order.get(change_groups[index].get("change_scale"), 0),
                            len(change_groups[index].get("files") or []),
                            sum(1 for point in iteration_test_requirements if point.get("change_group") == change_groups[index].get("name")),
                        ),
                        reverse=True,
                    )
                    summary_points = [summary_points[index] for index in ranked_indexes]
                    change_groups = [change_groups[index] for index in ranked_indexes]
                lines.extend(["## 本次迭代总结", "", f"### {iteration_summary.get('title')}", ""])
                for index, item in enumerate(summary_points, start=1):
                    group = change_groups[index - 1] if index <= len(change_groups) else {}
                    point_title = group.get("name") or f"变更点 {index}"
                    point_content = item
                    for separator in ("：", ":"):
                        prefix = f"{point_title}{separator}"
                        if point_content.startswith(prefix):
                            point_content = point_content[len(prefix):].strip()
                            break
                    lines.extend([f"### {index}. {point_title}", "", point_content, ""])
            lines.extend(["## 需求测试点", ""])
            for index, item in enumerate(iteration_test_requirements, 1):
                lines.extend([
                    f"### {index}. {item.get('title', '')}", "",
                    f"- 优先级：{item.get('priority', '')}",
                    "- 类型：迭代验证",
                    f"- 测试目标：{item.get('objective', '')}",
                    f"- 预期结果：{item.get('expected_result', '')}",
                    f"- 来源：{item.get('source_finding_key', '')}", "",
                ])
            lines.extend(["## 风险测试点", ""])
            for index, item in enumerate(risk_test_requirements, 1):
                reference = item.get("risk_reference") or {}
                lines.extend([
                    f"### {index}. {item.get('title', '')}", "",
                    f"- 优先级：{item.get('priority', '')}",
                    "- 类型：风险排查",
                    f"- 关联代码审查风险：{reference.get('title') or item.get('source_finding_key', '')}",
                    f"- 风险文件：`{reference.get('file', '')}`",
                    f"- 测试目标：{item.get('objective', '')}",
                    f"- 预期结果：{item.get('expected_result', '')}", "",
                ])
            lines.extend(["", "## 覆盖缺口", ""])
            lines.extend(f"- {item}" for item in report.get("coverage_gaps", []))
        filename = f"{'code-review-report' if report_type == 'change' else 'test-analysis-report'}-{task.id}.txt"
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

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        draft = self.get_object()
        if draft.status == "converted":
            return Response({"detail": "该测试需求已转为正式用例"}, status=status.HTTP_409_CONFLICT)
        draft.status = "accepted"
        draft.save(update_fields=["status"])
        return Response(self.get_serializer(draft).data)

    @action(detail=True, methods=["post"])
    def ignore(self, request, pk=None):
        draft = self.get_object()
        if draft.status == "converted":
            return Response({"detail": "已转正式用例的测试需求不能忽略"}, status=status.HTTP_409_CONFLICT)
        draft.status = "ignored"
        draft.save(update_fields=["status"])
        return Response(self.get_serializer(draft).data)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """将确认的测试需求转为项目正式用例，并保持可追溯关联。"""
        draft = self.get_object()
        if draft.converted_test_case_id:
            return Response({"detail": "该测试需求已转为正式用例", "test_case_id": draft.converted_test_case_id}, status=status.HTTP_409_CONFLICT)
        module_id = request.data.get("module_id")
        from testcases.models import TestCase, TestCaseModule, TestCaseStep
        module = TestCaseModule.objects.filter(pk=module_id, project=draft.task.project).first()
        if not module:
            return Response({"detail": "请选择当前项目下的用例模块"}, status=status.HTTP_400_BAD_REQUEST)
        level = {"high": "P0", "medium": "P1", "low": "P2"}.get(draft.priority, "P2")
        test_type = "security" if "安全" in draft.test_type else "permission" if "权限" in draft.test_type else "functional"
        with transaction.atomic():
            test_case = TestCase.objects.create(
                project=draft.task.project, module=module, creator=request.user,
                name=draft.title[:255], precondition=draft.objective,
                level=level, test_type=test_type,
                notes=f"来源：代码审查任务 {draft.task_id}；风险标识 {draft.source_finding_key or '综合分析'}",
            )
            TestCaseStep.objects.create(test_case=test_case, step_number=1, creator=request.user, description=draft.objective or "执行对应业务操作", expected_result=draft.expected_result or "相关功能符合需求")
            draft.status, draft.converted_test_case = "converted", test_case
            draft.save(update_fields=["status", "converted_test_case"])
        return Response({"draft": self.get_serializer(draft).data, "test_case_id": test_case.id}, status=status.HTTP_201_CREATED)
