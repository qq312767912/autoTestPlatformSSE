from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class OperationLog(models.Model):
    """
    用户操作日志模型
    """
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="操作用户",
        related_name="operation_logs"
    )
    username = models.CharField(max_length=150, blank=True, verbose_name="用户名")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP地址")
    user_agent = models.TextField(blank=True, verbose_name="User-Agent")
    path = models.CharField(max_length=255, verbose_name="请求路径")
    method = models.CharField(max_length=10, verbose_name="请求方法")
    module = models.CharField(max_length=100, blank=True, verbose_name="操作模块")
    action = models.CharField(max_length=255, blank=True, verbose_name="操作描述")
    request_data = models.TextField(blank=True, verbose_name="请求数据")
    response_code = models.IntegerField(null=True, blank=True, verbose_name="响应状态码")
    response_data = models.TextField(blank=True, verbose_name="响应数据")
    duration = models.IntegerField(verbose_name="执行耗时(毫秒)", default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="操作时间")

    class Meta:
        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.username} - {self.action} ({self.created_at})"


class OperationLogSetting(models.Model):
    """操作日志自动清理设置（单例）。"""

    retention_days = models.PositiveIntegerField(
        default=7,
        verbose_name="操作日志保留天数",
        help_text="自动清理超过保留天数的操作日志，默认 7 天",
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "操作日志设置"
        verbose_name_plural = "操作日志设置"

    def __str__(self):
        return f"操作日志保留 {self.retention_days} 天"

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(id=1, defaults={"retention_days": 7})
        return config


class AnonymizationRule(models.Model):
    """脱敏规则模型 - 支持动态管理 PII 识别正则"""

    name = models.CharField("规则名称", max_length=100, unique=True, help_text="规则的唯一标识名称")
    entity_type = models.CharField("实体类型", max_length=50, help_text="PII 类型，如 PHONE_NUMBER、EMAIL_ADDRESS")
    entity_label = models.CharField("显示标签", max_length=50, help_text="中文显示名称，如「手机号」「邮箱地址」")
    regex = models.TextField("正则表达式", help_text="用于匹配 PII 的正则表达式")
    score = models.FloatField(
        "置信度",
        default=0.80,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="匹配置信度，范围 0.0~1.0",
    )
    is_active = models.BooleanField("是否启用", default=True)
    description = models.TextField("规则说明", blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "脱敏规则"
        verbose_name_plural = "脱敏规则"
        ordering = ["-is_active", "entity_type"]

    def __str__(self):
        status = "启用" if self.is_active else "禁用"
        return f"{self.name} ({self.entity_label}) [{status}]"


class AnonymizationTemplate(models.Model):
    """脱敏模板模型 - 可复用的脱敏规则配置组合"""

    name = models.CharField("模板名称", max_length=100, unique=True)
    description = models.TextField("模板说明", blank=True, default="")
    enabled_preset_types = models.JSONField(
        "预设敏感信息类型",
        default=list,
        blank=True,
        help_text="如 ['PHONE_NUMBER', 'EMAIL_ADDRESS']",
    )
    custom_keywords = models.JSONField(
        "自定义关键词列表",
        default=list,
        blank=True,
        help_text="如 [{'keyword': '张三', 'replacement': '某某'}]",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="创建者",
        related_name='anonymization_templates',
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "脱敏模板"
        verbose_name_plural = "脱敏模板"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name}"


class AnonymizedDocument(models.Model):
    """文档脱敏记录模型 - 存储上传的文档及其脱敏结果"""

    STATUS_CHOICES = [
        ('pending', '待脱敏'),
        ('anonymizing', '脱敏中'),
        ('anonymized', '已脱敏'),
        ('failed', '脱敏失败'),
    ]

    original_file = models.FileField(
        "原始文件",
        upload_to='anonymization/original/%Y%m%d/',
        help_text="用户上传的原始文件",
    )
    original_filename = models.CharField("原始文件名", max_length=255)
    file_type = models.CharField("文件类型", max_length=10, help_text=".txt, .md, .docx")
    file_size = models.IntegerField("文件大小(bytes)", default=0)
    status = models.CharField(
        "脱敏状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
    )
    anonymized_file = models.FileField(
        "脱敏后文件",
        upload_to='anonymization/result/%Y%m%d/',
        null=True,
        blank=True,
    )
    anonymized_at = models.DateTimeField("脱敏时间", null=True, blank=True)
    anonymization_report = models.JSONField(
        "脱敏报告",
        null=True,
        blank=True,
        help_text="包含 total_count, details 等脱敏统计信息",
    )
    error_message = models.TextField("错误信息", blank=True, default='')

    # 每个文档独立的规则配置
    enabled_preset_types = models.JSONField(
        "启用的预设PII类型",
        default=list,
        blank=True,
        help_text="如 ['PHONE_NUMBER', 'EMAIL_ADDRESS', 'ID_CARD']",
    )
    custom_keywords = models.JSONField(
        "自定义关键词列表",
        default=list,
        blank=True,
        help_text="如 [{'keyword': '张三', 'replacement': '某某'}, {'keyword': '北京科技有限公司', 'replacement': '某公司'}]",
    )

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="上传者",
        related_name='anonymized_documents',
    )
    created_at = models.DateTimeField("上传时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "脱敏文档"
        verbose_name_plural = "脱敏文档"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} [{self.get_status_display()}]"
