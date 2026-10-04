"""正式金标资产：候选、双人标注、仲裁与不可变版本。"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TestAssetTaxonomy(models.Model):
    """由测试负责人维护、审批并版本化的业务分类与关键场景。"""

    STATE_CHOICES = [
        ("draft", "草稿"), ("review", "待审批"),
        ("published", "已发布"), ("retired", "已退役"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="test_asset_taxonomies",
    )
    scope_key = models.SlugField(max_length=64, db_index=True)
    version = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="draft", db_index=True)
    categories = models.JSONField(default=list, blank=True)
    critical_scenarios = models.JSONField(default=list, blank=True)
    maintained_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="maintained_test_asset_taxonomies",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_test_asset_taxonomies",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scope_key", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "scope_key", "version"],
                name="uniq_taxonomy_project_scope_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "state", "project_id", "scope_key", "version", "categories",
                "critical_scenarios", "content_hash",
            ).first()
            if previous and previous["state"] in {"published", "retired"}:
                protected = (
                    "project_id", "scope_key", "version", "categories",
                    "critical_scenarios", "content_hash",
                )
                if any(getattr(self, field) != previous[field] for field in protected):
                    raise ValidationError("已发布的分类版本不可修改，请创建后继版本")
                if previous["state"] == "retired" and self.state != "retired":
                    raise ValidationError("已退役的分类版本不能恢复")
        super().save(*args, **kwargs)


class GoldDataset(models.Model):
    STATUS_CHOICES = [("active", "启用"), ("archived", "归档")]
    SCOPE_CHOICES = [("general", "通用"), ("domain", "业务专用")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="gold_datasets"
    )
    name = models.CharField(max_length=255)
    task_type = models.CharField(max_length=32, db_index=True)
    description = models.TextField(blank=True)
    scope_type = models.CharField(max_length=16, choices=SCOPE_CHOICES, default="general")
    scope_key = models.SlugField(max_length=64, blank=True, default="", db_index=True)
    governance = models.JSONField(default=dict, blank=True)
    taxonomy_version = models.ForeignKey(
        TestAssetTaxonomy, on_delete=models.PROTECT, null=True, blank=True,
        related_name="gold_datasets",
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_gold_datasets",
    )
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
                fields=["project", "name", "task_type", "scope_key"],
                name="uniq_gold_dataset_project_name_task_scope",
            )
        ]
        indexes = [models.Index(fields=["project", "task_type", "status"])]

    def clean(self):
        if self.scope_type == "domain" and not self.scope_key:
            raise ValidationError({"scope_key": "业务专用数据集必须填写 scope_key"})
        if self.taxonomy_version_id:
            if self.taxonomy_version.project_id != self.project_id:
                raise ValidationError({"taxonomy_version": "分类版本不属于当前项目"})
            if self.taxonomy_version.state != "published":
                raise ValidationError({"taxonomy_version": "数据集只能绑定已发布的分类版本"})
            if self.scope_key and self.taxonomy_version.scope_key != self.scope_key:
                raise ValidationError({"taxonomy_version": "分类版本业务范围与数据集不一致"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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
    # 冻结时一并固化的治理策略快照（T05）：分类版本、分区口径、隐私与优化开关
    # 属于"这批样本是什么、能怎么用"的结论。若只冻结样本内容而治理策略仍可被
    # 事后修改，"冻结版本"就只是内容不可变、用途可变——评测口径会随之漂移。
    governance_snapshot = models.JSONField(default=dict, blank=True)
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
                "state", "content_hash", "sample_stats", "dataset_id", "version",
                "parent_version_id", "governance_snapshot",
            ).first()
            if previous and previous["state"] == "frozen":
                protected = (
                    "content_hash", "sample_stats", "dataset_id", "version",
                    "parent_version_id", "governance_snapshot",
                )
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
    ORIGIN_CHOICES = [
        ("history", "历史资料"), ("feedback", "人工反馈"),
        ("defect", "确认缺陷"), ("missed", "漏测"),
        ("false_positive", "误报"), ("rollback", "回滚"),
        ("manual", "人工创建"),
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
    candidate_origin = models.CharField(
        max_length=24, choices=ORIGIN_CHOICES, default="manual", db_index=True,
    )
    candidate_score = models.FloatField(default=0.0)
    recommended_split = models.CharField(max_length=20, choices=SPLIT_CHOICES, blank=True)
    recommended_tags = models.JSONField(default=list, blank=True)
    dedup_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    review_checklist = models.JSONField(default=dict, blank=True)
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
    # 审核结论的治理上下文（T05）：人工复核/仲裁不仅要给出答案，还要给出
    # 这批资产"归哪个分区、属于哪个业务分类、打什么标签"。这些结论原先只体现在
    # 最终样本上、看不出是谁在哪一轮定的，事后无法回答"这个分区是人定的还是
    # 候选预检建议的"。空值表示该轮没有改治理结论，沿用上一轮。
    tags = models.JSONField(default=list, blank=True)
    split = models.CharField(max_length=20, choices=GoldCase.SPLIT_CHOICES, blank=True)
    category = models.CharField(max_length=64, blank=True)
    # 审核时刻的输入/标准答案/证据指纹快照：用于证明"人当时审的就是这份材料"。
    review_snapshot = models.JSONField(default=dict, blank=True)
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
