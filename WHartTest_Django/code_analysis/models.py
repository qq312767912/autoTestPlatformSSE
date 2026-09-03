import base64
import hashlib
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from projects.models import Project


def _credential_cipher():
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class GitLabConnection(models.Model):
    name = models.CharField(max_length=100)
    base_url = models.URLField(max_length=500)
    verify_ssl = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class ProjectRepository(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="code_repositories")
    connection = models.ForeignKey(GitLabConnection, on_delete=models.PROTECT, related_name="repositories")
    gitlab_project_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    path_with_namespace = models.CharField(max_length=500)
    default_branch = models.CharField(max_length=255, default="main")
    languages = models.JSONField(default=list, blank=True)
    excluded_patterns = models.JSONField(default=list, blank=True)
    critical_annotations = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "connection", "gitlab_project_id")]
        ordering = ["name"]


class UserGitLabCredential(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="gitlab_credentials")
    connection = models.ForeignKey(GitLabConnection, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    encrypted_token = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "connection", "user")]

    def set_token(self, token):
        self.encrypted_token = _credential_cipher().encrypt(token.encode()).decode()

    def get_token(self):
        try:
            return _credential_cipher().decrypt(self.encrypted_token.encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise ValidationError("GitLab Token无法解密，请重新配置") from exc


class AnalysisTask(models.Model):
    SOURCE_CHOICES = [("merge_request", "Merge Request"), ("commits", "Commit比较")]
    STATUS_CHOICES = [
        ("pending", "待执行"), ("fetching", "获取代码中"), ("machine_analyzing", "机器分析中"),
        ("ai_analyzing", "AI分析中"), ("generating_tests", "生成测试报告中"),
        ("completed", "已完成"), ("partial", "部分完成"), ("failed", "失败"), ("cancelled", "已取消"),
    ]
    MODE_CHOICES = [("quick", "快速"), ("standard", "标准"), ("deep", "深度")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="code_analysis_tasks")
    repository = models.ForeignKey(ProjectRepository, on_delete=models.CASCADE, related_name="analysis_tasks")
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="code_analysis_tasks")
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    merge_request_iid = models.PositiveIntegerField(null=True, blank=True)
    base_sha = models.CharField(max_length=64, blank=True)
    head_sha = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=500, blank=True)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default="standard")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="pending", db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    change_report = models.JSONField(default=dict, blank=True)
    test_report = models.JSONField(default=dict, blank=True)
    raw_diff = models.TextField(blank=True)
    machine_coverage = models.FloatField(default=0)
    ai_coverage = models.FloatField(default=0)
    token_usage = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class TestRequirementDraft(models.Model):
    STATUS_CHOICES = [("draft", "AI草稿"), ("accepted", "已采纳"), ("ignored", "已忽略"), ("converted", "已转用例")]
    task = models.ForeignKey(AnalysisTask, on_delete=models.CASCADE, related_name="test_requirement_drafts")
    title = models.CharField(max_length=500)
    objective = models.TextField(blank=True)
    expected_result = models.TextField(blank=True)
    priority = models.CharField(max_length=20, default="medium")
    test_type = models.CharField(max_length=100, default="功能回归")
    source_finding_key = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)
