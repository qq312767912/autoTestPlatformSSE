"""内置公开测评集的数据资产模型。

与内部集（EvaluationSuite 归属项目）不同，这里的目录与数据版本是**全局只读资产**：
- `BenchmarkDatasetVersion` 发布后不可原地改写，更新只能产生新版本；
- `BenchmarkCase` 是统一协议下的样本，`is_clean` 是一等字段而非元数据，
  它决定样本进「误报分母」还是「召回分母」；
- `BenchmarkRepositorySnapshot` 记录隔离快照，带租约供运行期引用计数；
- `BenchmarkRunBinding` 把一次项目内运行锁定到数据版本 + 样本清单 + 模型 + 代码版本。
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from .benchmark_catalog import BENCHMARK_IDS, CLEAN_SOURCE_UNKNOWN


def benchmark_choices():
    return [(item, item) for item in BENCHMARK_IDS]


class ImmutableVersionError(RuntimeError):
    """已发布的数据版本不允许原地修改。"""


class BenchmarkDatasetVersion(models.Model):
    STATUS_CHOICES = [
        ("preparing", "准备中"),
        ("ready", "可运行"),
        ("failed", "准备失败"),
        ("deprecated", "已弃用"),
    ]
    ORIGIN_CHOICES = [
        ("network", "在线下载"),
        ("offline_package", "离线包预置"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    benchmark_id = models.CharField(max_length=32, choices=benchmark_choices(), db_index=True)
    version = models.CharField(max_length=64)
    upstream_revision = models.CharField(max_length=64)
    adapter_version = models.CharField(max_length=32)
    manifest_digest = models.CharField(max_length=64, db_index=True)
    manifest = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="preparing", db_index=True)
    origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default="network")
    case_total = models.PositiveIntegerField(default=0)
    case_valid = models.PositiveIntegerField(default=0)
    case_rejected = models.PositiveIntegerField(default=0)
    gold_total = models.PositiveIntegerField(default=0)
    clean_total = models.PositiveIntegerField(default=0)
    language_counts = models.JSONField(default=dict, blank=True)
    rejection_summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    deprecated_reason = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_benchmark_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["benchmark_id", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["benchmark_id", "version"], name="uniq_benchmark_version"
            ),
        ]
        indexes = [
            models.Index(fields=["benchmark_id", "status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.benchmark_id}@{self.version}"

    @property
    def is_publishable(self) -> bool:
        return self.status == "ready" and self.case_valid > 0

    def save(self, *args, **kwargs):
        if self._state.adding is False:
            previous = type(self).objects.filter(pk=self.pk).values().first() or {}
            if previous.get("status") == "ready":
                content_fields = {
                    "benchmark_id", "version", "upstream_revision", "adapter_version",
                    "manifest_digest", "manifest", "origin", "case_total", "case_valid",
                    "case_rejected", "gold_total", "clean_total", "language_counts",
                }
                changed = {
                    field for field in content_fields
                    if previous.get(field) != getattr(self, field)
                }
                dirty = set(kwargs.get("update_fields") or [])
                if changed or (dirty & content_fields):
                    raise ImmutableVersionError("已发布数据版本不可原地改写，请发布新版本")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == "ready":
            raise ImmutableVersionError("已发布数据版本不可删除，请改为弃用")
        return super().delete(*args, **kwargs)


class BenchmarkCase(models.Model):
    CONVERSION_CHOICES = [
        ("ok", "已转换"),
        ("rejected", "已拒绝"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        BenchmarkDatasetVersion, on_delete=models.CASCADE, related_name="cases",
    )
    benchmark_id = models.CharField(max_length=32, db_index=True)
    upstream_instance_id = models.CharField(max_length=255)
    repo = models.CharField(max_length=255, blank=True, db_index=True)
    base_sha = models.CharField(max_length=64, blank=True)
    head_sha = models.CharField(max_length=64, blank=True)
    language = models.CharField(max_length=16, blank=True, db_index=True)
    gold_issues = models.JSONField(default=list, blank=True)
    gold_count = models.PositiveIntegerField(default=0)
    # None 表示上游没有给出干净/不干净的判定，绝不能用「金标为空」推断为干净。
    is_clean = models.BooleanField(null=True, blank=True, db_index=True)
    is_clean_source = models.CharField(max_length=24, default=CLEAN_SOURCE_UNKNOWN)
    source_url = models.URLField(max_length=500, blank=True)
    conversion_status = models.CharField(
        max_length=16, choices=CONVERSION_CHOICES, default="ok", db_index=True,
    )
    conversion_error = models.TextField(blank=True)
    raw_ref = models.JSONField(default=dict, blank=True, help_text="原始 ID 与字段摘要，便于回溯")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version", "upstream_instance_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "upstream_instance_id"], name="uniq_benchmark_case_instance"
            ),
        ]
        indexes = [
            models.Index(fields=["version", "language"]),
            models.Index(fields=["version", "is_clean"]),
            models.Index(fields=["version", "conversion_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.benchmark_id}:{self.upstream_instance_id}"

    @property
    def case_key(self) -> str:
        return f"{self.benchmark_id}@{self.version.version}:{self.upstream_instance_id}"


class BenchmarkPrepareJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "排队中"),
        ("running", "准备中"),
        ("succeeded", "已完成"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    benchmark_id = models.CharField(max_length=32, choices=benchmark_choices(), db_index=True)
    version = models.ForeignKey(
        BenchmarkDatasetVersion, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="prepare_jobs",
    )
    requested_version = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True)
    stage = models.CharField(max_length=64, blank=True)
    origin = models.CharField(max_length=16, default="network")
    offline_package = models.CharField(max_length=500, blank=True)
    log = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    attempt = models.PositiveIntegerField(default=1)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="benchmark_prepare_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["benchmark_id", "status", "-created_at"])]

    def append_log(self, message: str) -> None:
        """追加可追溯日志；只保留末尾 20000 字符，避免无界增长。"""
        self.log = (self.log + "\n" + str(message)).strip()[-20000:]
        self.save(update_fields=["log"])


class BenchmarkRepositorySnapshot(models.Model):
    STATUS_CHOICES = [
        ("pending", "待准备"),
        ("ready", "可用"),
        ("failed", "失败"),
        ("purged", "已清理"),
    ]
    MAX_PATH_LENGTH = 500

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repo = models.CharField(max_length=255, db_index=True)
    base_sha = models.CharField(max_length=64)
    head_sha = models.CharField(max_length=64)
    path = models.CharField(max_length=MAX_PATH_LENGTH, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    lease_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["repo", "base_sha"]
        constraints = [
            models.UniqueConstraint(
                fields=["repo", "base_sha", "head_sha"], name="uniq_benchmark_snapshot_pair"
            ),
        ]
        indexes = [models.Index(fields=["repo", "status"])]

    def __str__(self) -> str:
        return f"{self.repo} {self.base_sha[:8]}..{self.head_sha[:8]}"


class BenchmarkRunBinding(models.Model):
    """把一次评测运行锁定到不可变的数据资产，报告据此复现。"""

    MODE_CHOICES = [
        ("pilot", "试跑"),
        ("full", "全量"),
    ]
    STATUS_CHOICES = [
        ("pending", "待运行"),
        ("running", "运行中"),
        ("completed", "已完成"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(
        "knowledge_evolution.EvaluationRun", on_delete=models.CASCADE,
        related_name="benchmark_binding",
    )
    version = models.ForeignKey(
        BenchmarkDatasetVersion, on_delete=models.PROTECT, related_name="run_bindings",
    )
    benchmark_id = models.CharField(max_length=32, db_index=True)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="pilot")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    selected_case_keys = models.JSONField(default=list, blank=True)
    excluded_cases = models.JSONField(default=list, blank=True)
    preflight = models.JSONField(default=dict, blank=True)
    snapshot_manifest = models.JSONField(default=list, blank=True)
    model_snapshot = models.JSONField(default=dict, blank=True)
    code_version = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["benchmark_id", "status", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.benchmark_id} {self.mode} run {self.run_id}"
