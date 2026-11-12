"""Hub 原材料的项目级独立测评与私有候选。"""
import uuid
from django.conf import settings
from django.db import models


class SkillAssessmentSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    source_version = models.ForeignKey('skills.SkillVersion', on_delete=models.RESTRICT)
    source_sha256 = models.CharField(max_length=64)
    mode = models.CharField(max_length=16)
    cases = models.JSONField(default=list)
    policy = models.JSONField(default=dict)
    rubric = models.JSONField(default=list)
    baseline = models.JSONField(default=dict)
    candidates = models.JSONField(default=list)
    winner_id = models.CharField(max_length=36, blank=True)
    state = models.CharField(max_length=24, default='ready')
    job_id = models.CharField(max_length=36, blank=True)
    error = models.TextField(blank=True)
    budget = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class SkillAgentTaskBinding(models.Model):
    session = models.OneToOneField('langgraph_integration.ChatSession', on_delete=models.CASCADE)
    source_version = models.ForeignKey('skills.SkillVersion', on_delete=models.RESTRICT)
    source_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
