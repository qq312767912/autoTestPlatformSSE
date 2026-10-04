"""历史资料导入、回放比对与项目级飞轮开关。"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ProjectFlywheelSetting(models.Model):
    project = models.OneToOneField(
        "projects.Project", on_delete=models.CASCADE, related_name="flywheel_setting",
    )
    enabled = models.BooleanField(default=False, db_index=True)
    rollout_note = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_flywheel_settings",
    )
    updated_at = models.DateTimeField(auto_now=True)


class HistoryImportBatch(models.Model):
    STATUS_CHOICES = [
        ("preflight", "已预检"), ("confirmed", "已确认"),
        ("completed", "已导入"), ("failed", "失败"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="history_import_batches",
    )
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="preflight", db_index=True)
    manifest = models.JSONField(default=dict)
    manifest_hash = models.CharField(max_length=64, db_index=True)
    preflight = models.JSONField(default=dict)
    candidate_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_history_import_batches",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="confirmed_history_import_batches",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(
            fields=["project", "manifest_hash"], name="uniq_history_manifest_project",
        )]


class HistoryImportItem(models.Model):
    ROLE_CHOICES = [
        ("requirement", "需求"), ("plan", "真实方案"), ("case", "真实用例"),
        ("execution", "执行结果"), ("report", "测试报告"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(HistoryImportBatch, on_delete=models.CASCADE, related_name="items")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    file = models.ForeignKey("file_management.FileAsset", on_delete=models.PROTECT)
    file_hash = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["batch", "role", "file"], name="uniq_history_batch_role_file",
        )]


class HistoryReplay(models.Model):
    STATUS_CHOICES = [
        ("draft", "草稿"), ("running", "回放中"), ("needs_review", "待判定"),
        ("passed", "通过"), ("blocked", "已阻断"), ("failed", "失败"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="history_replays")
    batch = models.ForeignKey(HistoryImportBatch, on_delete=models.PROTECT, related_name="replays")
    flywheel_run = models.OneToOneField(
        "knowledge_evolution.FlywheelRun", on_delete=models.PROTECT, related_name="history_replay",
    )
    gold_version = models.ForeignKey(
        "knowledge_evolution.GoldDatasetVersion", null=True, blank=True,
        on_delete=models.PROTECT, related_name="history_replays",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    execution_lock = models.JSONField(default=dict)
    config_hash = models.CharField(max_length=64, db_index=True)
    stage_scores = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    gate_report = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_history_replays",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)


class HistoryReplayDifference(models.Model):
    CATEGORY_CHOICES = [
        ("missing", "缺失"), ("extra", "新增"), ("conflict", "冲突"),
        ("equivalent", "等价"), ("uncertain", "不确定"),
    ]
    DECISION_CHOICES = [("equivalent", "等价"), ("regression", "退化"), ("acceptable", "可接受")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    replay = models.ForeignKey(HistoryReplay, on_delete=models.CASCADE, related_name="differences")
    stage = models.CharField(max_length=32, db_index=True)
    case_key = models.CharField(max_length=255, db_index=True)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, db_index=True)
    expected = models.JSONField(default=dict, blank=True)
    actual = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    critical = models.BooleanField(default=False, db_index=True)
    human_decision = models.CharField(max_length=16, choices=DECISION_CHOICES, blank=True)
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="decided_history_differences",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["stage", "case_key"]
