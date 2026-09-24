from django.conf import settings
from django.db import models

from .validators import normalize_hostname, validate_safe_ipv4


class TestHostMapping(models.Model):
    system_name = models.CharField("系统名称", max_length=100)
    hostname = models.CharField("域名", max_length=253, unique=True)
    ipv4 = models.GenericIPAddressField("IPv4", protocol="IPv4")
    enabled = models.BooleanField("是否启用", default=True)
    remark = models.CharField("备注", max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_test_host_mappings", verbose_name="创建人",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_test_host_mappings", verbose_name="更新人",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "test_host_mapping"
        ordering = ["system_name", "hostname"]
        verbose_name = "测试域名映射"
        verbose_name_plural = "测试域名映射"
        permissions = [
            ("publish_testhostconfig", "发布测试域名配置"),
            ("rollback_testhostconfig", "回滚测试域名配置"),
            ("diagnose_testhostmapping", "诊断测试域名映射"),
        ]

    def clean(self):
        self.hostname = normalize_hostname(self.hostname)
        self.ipv4 = validate_safe_ipv4(self.ipv4)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hostname} -> {self.ipv4}"


class TestHostConfigState(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    draft_revision = models.PositiveBigIntegerField(default=0)
    published_version = models.ForeignKey(
        "TestHostConfigVersion", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="active_states",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "test_host_config_state"

    @classmethod
    def get_state(cls):
        state, _ = cls.objects.get_or_create(singleton_key=1)
        return state


class TestHostConfigVersion(models.Model):
    STATUS_CHOICES = [
        ("publishing", "发布中"),
        ("published", "已发布"),
        ("failed", "发布失败"),
        ("superseded", "已被替代"),
    ]
    version = models.PositiveBigIntegerField(unique=True)
    source_draft_revision = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=list)
    checksum = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="publishing")
    source_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="rollback_versions"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="published_test_host_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "test_host_config_version"
        ordering = ["-version"]


class TestHostNodeStatus(models.Model):
    STATUS_CHOICES = [
        ("synced", "已同步"),
        ("pending", "待同步"),
        ("failed", "失败"),
    ]
    node_id = models.CharField(max_length=128, unique=True)
    node_type = models.CharField(max_length=32)
    display_name = models.CharField(max_length=128)
    applied_version = models.PositiveBigIntegerField(null=True, blank=True)
    applied_checksum = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    message = models.CharField(max_length=500, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "test_host_node_status"
        ordering = ["node_type", "node_id"]


class TestHostDiagnosis(models.Model):
    STATUS_CHOICES = [
        ("pending", "等待诊断"),
        ("running", "诊断中"),
        ("success", "诊断通过"),
        ("failed", "诊断失败"),
    ]
    mapping = models.ForeignKey(TestHostMapping, on_delete=models.CASCADE, related_name="diagnoses")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="test_host_diagnoses",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "test_host_diagnosis"
        ordering = ["-created_at"]
