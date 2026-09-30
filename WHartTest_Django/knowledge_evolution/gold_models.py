"""正式金标资产：候选、双人标注、仲裁与不可变版本。"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class GoldDataset(models.Model):
    STATUS_CHOICES = [("active", "启用"), ("archived", "归档")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="gold_datasets"
    )
    name = models.CharField(max_length=255)
    task_type = models.CharField(max_length=32, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="active", db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="owned_gold_datasets",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_gold_datasets",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "task_type"], name="uniq_gold_dataset_project_name_task"
            )
        ]
        indexes = [models.Index(fields=["project", "task_type", "status"])]


class GoldDatasetVersion(models.Model):
    STATE_CHOICES = [
        ("draft", "草稿"), ("labeling", "标注中"), ("review", "待复核"),
        ("frozen", "已冻结"), ("retired", "已退役"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(GoldDataset, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="draft", db_index=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    sample_stats = models.JSONField(default=dict, blank=True)
    parent_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="successors"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_gold_dataset_versions",
    )
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="frozen_gold_dataset_versions",
    )
    frozen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["dataset", "version"], name="uniq_gold_dataset_version")
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "state", "content_hash", "sample_stats", "dataset_id", "version", "parent_version_id"
            ).first()
            if previous and previous["state"] == "frozen":
                protected = ("content_hash", "sample_stats", "dataset_id", "version", "parent_version_id")
                if self.state != "retired" and self.state != "frozen":
                    raise ValidationError("冻结版本只能退役，不能重新编辑")
                if any(getattr(self, key) != previous[key] for key in protected):
                    raise ValidationError("冻结版本内容不可修改，请创建后继版本")
        super().save(*args, **kwargs)


class GoldCase(models.Model):
    SPLIT_CHOICES = [
        ("gold", "Gold"), ("regression", "Regression"), ("fresh", "Fresh"),
        ("challenge", "Challenge"), ("hidden", "Hidden"),
    ]
    STATE_CHOICES = [
        ("candidate", "候选"), ("labeling", "标注中"), ("conflict", "待仲裁"),
        ("confirmed", "已确认"), ("rejected", "已拒绝"),
    ]
    PRIVACY_CHOICES = [
        ("internal", "内部"), ("restricted", "受限"), ("prohibited", "禁止用于优化"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(GoldDatasetVersion, on_delete=models.CASCADE, related_name="cases")
    source_output = models.ForeignKey(
        "knowledge_evolution.GenerationOutput", null=True, blank=True,
        on_delete=models.PROTECT, related_name="gold_cases",
    )
    source_feedback = models.ForeignKey(
        "knowledge_evolution.FeedbackEvent", null=True, blank=True,
        on_delete=models.PROTECT, related_name="gold_cases",
    )
    task_type = models.CharField(max_length=32, db_index=True)
    title = models.CharField(max_length=255)
    input_snapshot = models.JSONField(default=dict, blank=True)
    expected_output = models.JSONField(default=dict, blank=True)
    rubric = models.JSONField(default=dict, blank=True)
    required_items = models.JSONField(default=list, blank=True)
    forbidden_items = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    split = models.CharField(max_length=20, choices=SPLIT_CHOICES, default="fresh", db_index=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default="candidate", db_index=True)
    difficulty = models.CharField(max_length=32, blank=True)
    risk_level = models.CharField(max_length=32, blank=True)
    privacy_level = models.CharField(
        max_length=20, choices=PRIVACY_CHOICES, default="internal", db_index=True
    )
    allow_optimization = models.BooleanField(default=True)
    source_hash = models.CharField(max_length=64, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_gold_cases",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["version", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["version", "source_hash"], name="uniq_gold_case_version_source")
        ]
        indexes = [
            models.Index(fields=["version", "split", "state"]),
            models.Index(fields=["task_type", "state"]),
        ]

    def clean(self):
        if self.privacy_level == "prohibited" and self.allow_optimization:
            raise ValidationError({"allow_optimization": "禁止用于优化的数据不能开启优化使用"})
        if self.version_id and self.version.state == "frozen":
            raise ValidationError("冻结版本中的样本不可修改")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class GoldAnnotation(models.Model):
    ROUND_CHOICES = [("primary", "初标"), ("review", "复核"), ("arbitration", "仲裁")]
    CONCLUSION_CHOICES = [("accepted", "通过"), ("rejected", "拒绝"), ("needs_changes", "需修改")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(GoldCase, on_delete=models.CASCADE, related_name="annotations")
    round = models.CharField(max_length=16, choices=ROUND_CHOICES)
    answer = models.JSONField(default=dict, blank=True)
    rubric_scores = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    conclusion = models.CharField(max_length=20, choices=CONCLUSION_CHOICES)
    comment = models.TextField(blank=True)
    annotator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gold_annotations"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["case", "round"], name="uniq_gold_annotation_round")
        ]


class AnnotationConflict(models.Model):
    STATE_CHOICES = [("open", "待仲裁"), ("resolved", "已解决")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(GoldCase, on_delete=models.CASCADE, related_name="annotation_conflict")
    primary_annotation = models.ForeignKey(
        GoldAnnotation, on_delete=models.PROTECT, related_name="primary_conflicts"
    )
    review_annotation = models.ForeignKey(
        GoldAnnotation, on_delete=models.PROTECT, related_name="review_conflicts"
    )
    differing_fields = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="open", db_index=True)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="resolved_gold_conflicts",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
