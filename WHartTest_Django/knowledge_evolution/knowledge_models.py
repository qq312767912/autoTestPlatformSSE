"""知识资产化的核心数据模型（specs/knowledge-flywheel/tasks.md 任务 4）。

对应设计文档 design.md §4.1 存储架构、§5 知识加工流程、§5.3 分级与有效期：

- SourceSnapshot    不可变来源快照（幂等键：source_type + source_id + revision + content_hash）
- KnowledgeAsset    稳定业务标识（project + type + key 唯一）
- KnowledgeVersion  不可变发布版本（含有效期与审批链）
- KnowledgeEvidence 资产与证据关联（支持/派生/反驳/矛盾）
- KnowledgeCandidate 自动提炼的待审候选（去重键 + 状态机）
- KnowledgeConflict 矛盾、时效或适用域冲突
- IndexProjection   Qdrant/图谱投影一致性（含换 embedding 模型的投影版本）
- KnowledgeAuditLog 状态流转审计（R9：操作人、理由、前后版本、时间）

单独成文件而非并入 models.py，是为了把「已完成的任务 1/2」与「任务 4 新增」
在版本控制上分开，避免大块重写同一文件。
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# --------------------------------------------------------------------------- 常量

#: 知识分级（design.md §5.3）
LEVEL_CHOICES = [
    ("L1", "L1 强制"),
    ("L2", "L2 应当参考"),
    ("L3", "L3 可选参考"),
]

#: 默认复核间隔（天），design.md §5.3
DEFAULT_REVIEW_DAYS = {"L1": 90, "L2": 180, "L3": 90}

#: 视作「权威来源」的 authority 取值（R2：L1 必须有权威证据）
AUTHORITATIVE_LEVELS = frozenset({"official", "standard", "approved"})

ACTOR_TYPE_CHOICES = [
    ("user", "用户"),
    ("system", "系统"),
    ("integration", "集成"),
]


def _check_transition(transitions: dict, current: str, target: str, label: str) -> None:
    allowed = transitions.get(current)
    if allowed is None:
        raise ValidationError(f"{label}：未知的当前状态 {current!r}")
    if target not in allowed:
        raise ValidationError(
            f"{label}：不允许从 {current!r} 流转到 {target!r}（允许：{sorted(allowed) or '无，终态'}）"
        )


def _acl_q(granted_tags) -> models.Q:
    """ACL 过滤条件：无标签视为项目内公开，否则需与持有标签有交集。

    设计 §4.2 要求 payload 带 acl_tags、R9 要求「在召回前过滤 ACL」。
    这里用 JSONField 而非 ArrayField，与现有模型（channels/candidates 等）保持一致。
    """
    query = models.Q(acl_tags=[]) | models.Q(acl_tags__isnull=True)
    for tag in granted_tags or []:
        query |= models.Q(acl_tags__contains=[tag])
    return query


class ACLQuerySet(models.QuerySet):
    def visible_with(self, granted_tags):
        return self.filter(_acl_q(granted_tags))


class ACLMixin(models.Model):
    """项目内可见性标签。空列表 = 项目内公开。"""

    objects = ACLQuerySet.as_manager()

    acl_tags = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True
        default_manager_name = "objects"

    @property
    def is_public_to_project(self) -> bool:
        return not self.acl_tags

    def allows(self, granted_tags) -> bool:
        if self.is_public_to_project:
            return True
        return bool(set(self.acl_tags) & set(granted_tags or []))


class StateMachineMixin(models.Model):
    """声明式状态机：子类给出 STATE_FIELD / STATE_TRANSITIONS / STATE_LABEL。"""

    STATE_FIELD = "state"
    STATE_TRANSITIONS: dict[str, set[str]] = {}
    STATE_LABEL = "状态"

    class Meta:
        abstract = True

    @classmethod
    def state_field_name(cls) -> str:
        return cls.STATE_FIELD

    def current_state(self) -> str:
        return getattr(self, self.STATE_FIELD)

    def can_transition_to(self, target: str) -> bool:
        return target in self.STATE_TRANSITIONS.get(self.current_state(), set())

    def available_transitions(self) -> list[str]:
        return sorted(self.STATE_TRANSITIONS.get(self.current_state(), set()))

    def transition_to(self, target: str, *, actor=None, reason: str = "", save: bool = True):
        current = self.current_state()
        _check_transition(self.STATE_TRANSITIONS, current, target, self.STATE_LABEL)
        setattr(self, self.STATE_FIELD, target)
        if save:
            self.save(update_fields=[self.STATE_FIELD, "updated_at"])
            KnowledgeAuditLog.record(
                project_id=self.project_id,
                actor=actor,
                action="transition",
                entity=self,
                from_state=current,
                to_state=target,
                reason=reason,
            )
        return self


# ------------------------------------------------------------------- 1. 来源快照


class SourceSnapshot(ACLMixin, StateMachineMixin):
    """不可变来源快照（R1）。幂等键：source_type + source_id + revision + content_hash。"""

    SOURCE_TYPE_CHOICES = [
        ("document", "文档"),
        ("code_commit", "代码快照"),
        ("requirement", "需求"),
        ("test_case", "测试用例"),
        ("test_run", "测试执行"),
        ("defect", "缺陷"),
        ("review_result", "审查结果"),
        ("manual_review", "人工评审"),
        ("conversation", "对话"),
        ("experience", "经验蒸馏"),
    ]
    STATUS_CHOICES = [
        ("captured", "已采集"),
        ("parsed", "已解析"),
        ("extracted", "已提取"),
        ("failed", "处理失败"),
        ("superseded", "已被新快照取代"),
    ]
    STATE_FIELD = "status"
    STATE_LABEL = "来源快照状态"
    STATE_TRANSITIONS = {
        "captured": {"parsed", "failed"},
        "parsed": {"extracted", "failed"},
        "extracted": {"superseded", "failed"},
        "failed": {"captured", "parsed"},
        "superseded": set(),
    }
    AUTHORITY_CHOICES = [
        ("official", "官方/权威来源"),
        ("standard", "标准/规范"),
        ("approved", "已审批内部来源"),
        ("internal", "内部一般来源"),
        ("external", "外部参考"),
        ("derived", "派生生成"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_snapshots"
    )
    knowledge_base = models.ForeignKey(
        "knowledge.KnowledgeBase", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_snapshots",
    )
    source_type = models.CharField(max_length=32, choices=SOURCE_TYPE_CHOICES, db_index=True)
    source_id = models.CharField(max_length=255, db_index=True)
    revision = models.CharField(max_length=255, blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    authority = models.CharField(
        max_length=20, choices=AUTHORITY_CHOICES, default="internal", db_index=True
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="captured", db_index=True
    )
    location = models.JSONField(
        default=dict, blank=True,
        help_text="来源定位：页码/章节/表格位置，或 repo+commit+file+line",
    )
    raw_content = models.TextField(blank=True)
    parsed_text = models.TextField(blank=True)
    parser_version = models.CharField(max_length=100, blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_snapshots",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_id", "revision", "content_hash"],
                name="uniq_snapshot_source_revision_hash",
            )
        ]
        indexes = [
            models.Index(fields=["project", "source_type", "created_at"]),
            models.Index(fields=["project", "authority"]),
        ]
        permissions = [
            ("export_sourcesnapshot", "Can export knowledge source snapshots"),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.source_id}@{self.revision or '-'}"

    @property
    def is_authoritative(self) -> bool:
        return self.authority in AUTHORITATIVE_LEVELS

    @classmethod
    def make_idempotency_key(cls, source_type, source_id, revision, content_hash) -> str:
        return f"{source_type}:{source_id}:{revision or '-'}:{content_hash}"

    @property
    def idempotency_key(self) -> str:
        return self.make_idempotency_key(
            self.source_type, self.source_id, self.revision, self.content_hash
        )

    def clean(self):
        if not self.source_id:
            raise ValidationError("来源快照必须带 source_id")
        if not self.content_hash:
            raise ValidationError("来源快照必须带 content_hash，否则无法判定幂等")

    @classmethod
    def get_or_create(cls, *, project, source_type, source_id, revision="", content_hash="",
                      defaults=None, **kwargs):
        """按幂等键查询或创建。defaults 只在创建时使用。"""
        revision = revision or ""
        content_hash = content_hash or ""
        if not content_hash:
            raise ValidationError("来源快照必须带 content_hash")
        try:
            return cls.objects.get(
                project=project, source_type=source_type, source_id=source_id,
                revision=revision, content_hash=content_hash,
            ), False
        except cls.DoesNotExist:
            merged_defaults = dict(defaults or {})
            merged_defaults.update(kwargs)
            return cls.objects.create(
                project=project, source_type=source_type, source_id=source_id,
                revision=revision, content_hash=content_hash, **merged_defaults
            ), True


# ------------------------------------------------------------------- 2. 知识资产


class KnowledgeAsset(ACLMixin):
    """稳定业务标识（R2）。投影里靠 asset.key 做别名归一。"""

    ASSET_TYPE_CHOICES = [
        ("concept", "概念"),
        ("rule", "规则/约束"),
        ("fact", "事实"),
        ("procedure", "流程/步骤"),
        ("failure_case", "故障案例"),
        ("standard", "规范条文"),
    ]
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("active", "生效中"),
        ("review_due", "待复核"),
        ("deprecated", "已废弃"),
        ("archived", "已归档"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_assets"
    )
    asset_type = models.CharField(max_length=32, choices=ASSET_TYPE_CHOICES, db_index=True)
    key = models.CharField(max_length=255, db_index=True, help_text="归一化后的稳定业务标识")
    title = models.CharField(max_length=500)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, default="L3", db_index=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True
    )
    current_version = models.ForeignKey(
        "knowledge_evolution.KnowledgeVersion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="current_of_assets",
    )
    applicability = models.JSONField(
        default=dict, blank=True,
        help_text="适用域：产品线/版本/环境/语言等限定条件",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="owned_knowledge_assets",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_knowledge_assets",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_knowledge_assets",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_type", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "asset_type", "key"], name="uniq_asset_project_type_key"
            )
        ]
        indexes = [
            models.Index(fields=["project", "level", "status"]),
            models.Index(fields=["project", "status", "updated_at"]),
        ]
        permissions = [
            ("publish_knowledgeasset", "Can publish a knowledge asset version"),
            ("resolve_knowledgeconflict", "Can resolve a knowledge conflict"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.key}"

    @property
    def review_interval_days(self) -> int:
        return DEFAULT_REVIEW_DAYS.get(self.level, 90)

    @property
    def requires_dual_approval(self) -> bool:
        """L1 需业务专家 + 项目管理员双审（需求 §6 默认产品决策 1）。"""
        return self.level == "L1"

    def next_version_number(self) -> int:
        last = self.versions.order_by("-version").values_list("version", flat=True).first()
        return (last or 0) + 1

    def set_current_version(self, version, *, actor=None, reason: str = "") -> None:
        if version.asset_id != self.id:
            raise ValidationError("只能把本资产的版本设为 current_version")
        previous_status = self.status
        self.current_version = version
        self.status = "active" if version.status == "published" else self.status
        self.updated_by = actor
        self.save(update_fields=["current_version", "status", "updated_by", "updated_at"])
        KnowledgeAuditLog.record(
            project_id=self.project_id,
            actor=actor,
            action="transition",
            entity=self,
            from_state=previous_status,
            to_state=self.status,
            reason=reason or f"切换当前版本至 v{version.version}",
        )


# ------------------------------------------------------------------- 3. 知识版本


class KnowledgeVersion(StateMachineMixin, models.Model):
    """不可变发布版本（R1/R2/R3）。状态机见 design.md §5。"""

    STATUS_CHOICES = [
        ("pending_eval", "待评测"),
        ("awaiting_approval", "待审批"),
        ("published", "已发布"),
        ("review_due", "待复核"),
        ("deprecated", "已废弃"),
        ("rolled_back", "已回滚"),
        ("rejected", "已驳回"),
    ]
    STATE_FIELD = "status"
    STATE_LABEL = "知识版本状态"
    STATE_TRANSITIONS = {
        "pending_eval": {"awaiting_approval", "rejected"},
        "awaiting_approval": {"published", "rejected"},
        "published": {"review_due", "deprecated", "rolled_back"},
        "review_due": {"published", "deprecated", "rolled_back"},
        "rolled_back": {"published"},
        "deprecated": set(),
        "rejected": set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        KnowledgeAsset, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    content = models.TextField(help_text="知识原子：对象 + 约束/行为 + 条件 + 适用域")
    content_hash = models.CharField(max_length=64, db_index=True)
    source_snapshot = models.ForeignKey(
        SourceSnapshot, on_delete=models.PROTECT, related_name="versions"
    )
    previous_version = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="successors"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending_eval", db_index=True
    )
    valid_from = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_knowledge_versions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    second_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="co_approved_knowledge_versions",
        help_text="L1 强制知识的第二审批人",
    )
    second_approved_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_knowledge_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "version"], name="uniq_version_asset_version"
            )
        ]
        indexes = [
            models.Index(fields=["asset", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]
        permissions = [
            ("approve_l1_knowledgeversion", "Can approve an L1 knowledge version"),
        ]

    def __str__(self) -> str:
        return f"{self.asset.key} v{self.version} ({self.status})"

    @property
    def project_id(self):
        return self.asset.project_id

    @property
    def level(self) -> str:
        return self.asset.level

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def review_interval_days(self) -> int:
        return self.asset.review_interval_days

    @property
    def acl_tags(self):
        """版本自身不存 ACL，继承资产（design.md §4.2：payload 需带 acl_tags）。"""
        return self.asset.acl_tags

    def allows(self, granted_tags) -> bool:
        return self.asset.allows(granted_tags)

    def supporting_evidence(self):
        return self.evidences.filter(relation__in=["supported_by", "derived_from"])

    def has_authoritative_evidence(self) -> bool:
        return self.supporting_evidence().filter(
            snapshot__authority__in=sorted(AUTHORITATIVE_LEVELS)
        ).exists()

    def clean(self):
        if self.version is not None and self.version < 1:
            raise ValidationError("版本号必须从 1 开始")
        if self.asset_id and self.source_snapshot_id:
            if self.asset.project_id != self.source_snapshot.project_id:
                raise ValidationError("知识版本与来源快照必须属于同一项目")
        if self.expires_at and self.valid_from and self.expires_at <= self.valid_from:
            raise ValidationError("失效时间必须晚于生效时间")
        if self.status == "published" and not self.approved_by_id:
            raise ValidationError("未记录审批人的版本不得为已发布状态")
        if self.status == "rejected" and not self.change_reason:
            raise ValidationError("驳回必须写明理由（R9 审计要求）")

    def set_validity(self, *, valid_from=None, review_days: int | None = None) -> None:
        start = valid_from or timezone.now()
        days = self.review_interval_days if review_days is None else review_days
        self.valid_from = start
        self.expires_at = start + timezone.timedelta(days=days)

    def approve(self, *, actor, second_approver=None, reason: str = "") -> None:
        """记录审批。L1 强制知识必须两人（需求 §6）。审批后版本进入 awaiting_approval。"""
        if self.status not in {"awaiting_approval", "pending_eval"}:
            raise ValidationError(f"状态 {self.status} 不接受审批")
        if self.asset.requires_dual_approval:
            if not second_approver:
                raise ValidationError("L1 强制知识需要第二审批人")
            if actor.pk == second_approver.pk:
                raise ValidationError("L1 双重审批必须由两名不同用户完成")
        now = timezone.now()
        self.approved_by = actor
        self.approved_at = now
        if second_approver:
            self.second_approver = second_approver
            self.second_approved_at = now
        if reason:
            self.change_reason = reason
        # 从 pending_eval 进入 awaiting_approval，显式状态流转并审计
        if self.status == "pending_eval":
            self.transition_to(
                "awaiting_approval", actor=actor, reason=reason or "审批进入 awaiting_approval", save=False
            )
        self.save(
            update_fields=[
                "status", "approved_by", "approved_at", "second_approver",
                "second_approved_at", "change_reason", "updated_at",
            ]
        )

    def publish(self, *, actor, reason: str = "", second_approver=None) -> None:
        """晋级门禁的生产侧落点。

        硬约束（R2）：L1 必须有权威证据 + 双人审批，否则不得发布。
        这里在「发布」这一动作上校验，而不是 clean() —— 因为版本创建时证据尚未挂载。
        """
        _check_transition(self.STATE_TRANSITIONS, self.status, "published", self.STATE_LABEL)
        if second_approver and not self.second_approver_id:
            self.approve(actor=actor, second_approver=second_approver, reason=reason)
        if not self.approved_by_id:
            raise ValidationError("发布前必须先审批")
        if self.asset.requires_dual_approval:
            if not self.second_approver_id:
                raise ValidationError("L1 强制知识缺少第二审批人，不得发布")
            if not self.has_authoritative_evidence():
                raise ValidationError("L1 强制知识缺少权威来源证据，不得发布")

        self.status = "published"
        self.set_validity(valid_from=self.valid_from or timezone.now())
        self.asset.set_current_version(self, actor=actor, reason=reason)
        self.save(update_fields=["status", "valid_from", "expires_at", "updated_at"])
        KnowledgeAuditLog.record(
            project_id=self.project_id, actor=actor, action="publish", entity=self,
            from_state="awaiting_approval", to_state="published", reason=reason,
            after_version=self.version,
        )

    def rollback(self, *, actor, reason: str) -> None:
        """回滚不重建原始数据，只切状态与 active release（R7）。"""
        _check_transition(self.STATE_TRANSITIONS, self.status, "rolled_back", self.STATE_LABEL)
        if not reason:
            raise ValidationError("回滚必须写明理由")
        previous = self.status
        self.status = "rolled_back"
        self.save(update_fields=["status", "updated_at"])
        target = self.previous_version
        if target and target.status == "published":
            self.asset.set_current_version(target, actor=actor, reason=reason)
        KnowledgeAuditLog.record(
            project_id=self.project_id, actor=actor, action="rollback", entity=self,
            from_state=previous, to_state="rolled_back", reason=reason,
            after_version=target.version if target else None,
        )

    def mark_review_due(self, *, actor=None, reason: str = "") -> None:
        if not self.can_transition_to("review_due"):
            return
        self.transition_to("review_due", actor=actor, reason=reason or "到期复核")
        self.asset.status = "review_due"
        self.asset.save(update_fields=["status", "updated_at"])


# ------------------------------------------------------------------- 4. 证据关联


class KnowledgeEvidence(models.Model):
    """资产与证据的关联（R1：可追溯来源快照与具体位置）。"""

    RELATION_CHOICES = [
        ("supported_by", "由…支持"),
        ("derived_from", "派生自"),
        ("refuted_by", "被…反驳"),
        ("contradicts", "与…矛盾"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        KnowledgeVersion, on_delete=models.CASCADE, related_name="evidences"
    )
    snapshot = models.ForeignKey(
        SourceSnapshot, on_delete=models.PROTECT, related_name="evidences"
    )
    relation = models.CharField(
        max_length=20, choices=RELATION_CHOICES, default="supported_by", db_index=True
    )
    location = models.JSONField(default=dict, blank=True)
    location_hash = models.CharField(max_length=64, blank=True, db_index=True)
    excerpt = models.TextField(blank=True)
    weight = models.FloatField(default=1.0)
    extractor_version = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-weight", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "snapshot", "relation", "location_hash"],
                name="uniq_evidence_version_snapshot_relation_loc",
            )
        ]
        indexes = [
            models.Index(fields=["version", "relation"]),
            models.Index(fields=["snapshot", "relation"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_relation_display()} {self.snapshot_id}"

    def _compute_location_hash(self) -> str:
        import hashlib, json

        payload = json.dumps(self.location or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        self.location_hash = self._compute_location_hash()
        super().save(*args, **kwargs)

    def clean(self):
        if self.version_id and self.snapshot_id:
            if self.version.project_id != self.snapshot.project_id:
                raise ValidationError("证据与知识版本必须属于同一项目（R9 跨项目隔离）")
        if self.weight < 0:
            raise ValidationError("证据权重不得为负")

    @classmethod
    def accessible_for(cls, granted_tags):
        """R9：无权访问的证据不得进入摘要、图关系或日志。"""
        return cls.objects.filter(snapshot__in=SourceSnapshot.objects.visible_with(granted_tags))


# ------------------------------------------------------------------- 5. 知识候选


class KnowledgeCandidate(StateMachineMixin):
    """自动提炼的待审候选（R2/R6）。设计原则 3：候选与生产分离。"""

    KIND_CHOICES = [
        ("concept", "概念"),
        ("rule", "规则/约束"),
        ("relation", "关系"),
        ("summary", "摘要"),
        ("knowledge_atom", "知识原子"),
        ("experience", "二次经验"),
    ]
    ORIGIN_CHOICES = [
        ("extraction", "结构提取"),
        ("distillation", "反馈蒸馏"),
        ("manual", "人工录入"),
        ("evaluation_failure", "评测失败样本"),
    ]
    STATE_CHOICES = [
        ("pending", "待处理"),
        ("conflicted", "存在冲突"),
        ("evaluating", "评测中"),
        ("awaiting_approval", "待审批"),
        ("accepted", "已采纳"),
        ("rejected", "已驳回"),
        ("merged", "已合并到既有资产"),
    ]
    STATE_TRANSITIONS = {
        "pending": {"conflicted", "evaluating", "rejected", "merged"},
        "conflicted": {"pending", "evaluating", "rejected"},
        "evaluating": {"awaiting_approval", "rejected"},
        "awaiting_approval": {"accepted", "rejected"},
        "accepted": set(),
        "rejected": set(),
        "merged": set(),
    }
    STATE_FIELD = "state"
    STATE_LABEL = "知识候选状态"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_candidates"
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES, default="extraction")
    payload = models.JSONField(default=dict, blank=True)
    level = models.CharField(
        max_length=2, choices=LEVEL_CHOICES, default="L3",
        help_text="候选建议的分级，需审批确认",
    )
    confidence = models.FloatField(default=0.5)
    source_snapshot = models.ForeignKey(
        SourceSnapshot, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="candidates",
    )
    evidence = models.JSONField(
        default=list, blank=True,
        help_text="候选阶段的证据引用：snapshot_id + location + relation + weight",
    )
    feedback_event_ids = models.JSONField(
        default=list, blank=True, help_text="二次蒸馏候选的来源反馈事件（R6 四级关联）",
    )
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default="pending", db_index=True
    )
    dedup_key = models.CharField(
        max_length=255, unique=True,
        help_text="去重键：project + kind + 归一化内容哈希",
    )
    promoted_asset = models.ForeignKey(
        KnowledgeAsset, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="promoted_candidates",
    )
    extracted_by = models.CharField(max_length=100, blank=True, help_text="提取器版本")
    prompt_version = models.CharField(max_length=128, blank=True)
    review_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_knowledge_candidates",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_knowledge_candidates",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "state", "created_at"]),
            models.Index(fields=["project", "kind", "state"]),
        ]
        permissions = [
            ("review_knowledgecandidate", "Can review a knowledge candidate"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} candidate ({self.state})"

    @property
    def is_promotable(self) -> bool:
        """设计原则 3：只有通过门禁的候选才能进入资产。"""
        return self.state in {"accepted"} and self.promoted_asset_id is not None

    def accept(self, *, actor, asset=None, reason: str = "") -> None:
        """采纳候选。仅当已产出资产时允许 —— 候选本身不能直接当生产知识。"""
        if asset is None:
            raise ValidationError("采纳候选必须同时给出落地的知识资产，不得以候选直接充当生产知识")
        if asset.project_id != self.project_id:
            raise ValidationError("候选与落地资产必须属于同一项目")
        _check_transition(self.STATE_TRANSITIONS, self.state, "accepted", self.STATE_LABEL)
        previous = self.state
        self.state = "accepted"
        self.promoted_asset = asset
        self.reviewed_by = actor
        self.reviewed_at = timezone.now()
        self.review_reason = reason
        self.save(
            update_fields=[
                "state", "promoted_asset", "reviewed_by",
                "reviewed_at", "review_reason", "updated_at",
            ]
        )
        KnowledgeAuditLog.record(
            project_id=self.project_id, actor=actor, action="transition", entity=self,
            from_state=previous, to_state="accepted", reason=reason,
        )

    def clean(self):
        if self.confidence < 0 or self.confidence > 1:
            raise ValidationError("置信度必须落在 0~1")
        if self.level == "L1" and self.origin == "distillation" and not self.evidence:
            raise ValidationError("经验蒸馏不得单独产出 L1 候选（R6：不得直接改写生产知识）")
        if self.promoted_asset_id and self.promoted_asset.project_id != self.project_id:
            raise ValidationError("候选与落地资产必须属于同一项目")

    @staticmethod
    def build_dedup_key(project_id, kind: str, normalized_content: str) -> str:
        import hashlib

        digest = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        return f"{project_id}:{kind}:{digest}"


# ------------------------------------------------------------------- 6. 知识冲突


class KnowledgeConflict(StateMachineMixin, models.Model):
    """矛盾、时效或适用域冲突（R3）。未解决前降低检索信任。"""

    TYPE_CHOICES = [
        ("contradiction", "结论矛盾"),
        ("temporal", "时效冲突"),
        ("scope_overlap", "适用域重叠"),
        ("duplicate", "近重复"),
    ]
    STATE_CHOICES = [
        ("open", "待处理"),
        ("resolving", "处理中"),
        ("resolved", "已解决"),
        ("dismissed", "已忽略"),
    ]
    STATE_TRANSITIONS = {
        "open": {"resolving", "dismissed"},
        "resolving": {"resolved", "open"},
        "resolved": set(),
        "dismissed": set(),
    }
    STATE_FIELD = "state"
    STATE_LABEL = "知识冲突状态"
    RESOLUTION_CHOICES = [
        ("pending", "未裁决"),
        ("prefer_left", "采信左"),
        ("prefer_right", "采信右"),
        ("merge", "合并"),
        ("coexist", "限定适用域后共存"),
        ("both_deprecated", "双方废弃"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_conflicts"
    )
    left_asset = models.ForeignKey(
        KnowledgeAsset, on_delete=models.PROTECT, related_name="conflicts_as_left"
    )
    right_asset = models.ForeignKey(
        KnowledgeAsset, on_delete=models.PROTECT, related_name="conflicts_as_right"
    )
    left_version = models.ForeignKey(
        KnowledgeVersion, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="conflicts_as_left",
    )
    right_version = models.ForeignKey(
        KnowledgeVersion, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="conflicts_as_right",
    )
    conflict_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default="open", db_index=True
    )
    resolution = models.CharField(
        max_length=20, choices=RESOLUTION_CHOICES, default="pending", db_index=True
    )
    trust_penalty = models.FloatField(
        default=0.5, help_text="未解决期间检索信任折减系数（R3）"
    )
    detail = models.TextField(blank=True)
    resolution_note = models.TextField(blank=True)
    detected_by = models.CharField(max_length=100, blank=True)
    detector_version = models.CharField(max_length=100, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="resolved_knowledge_conflicts",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["left_asset", "right_asset", "conflict_type"],
                name="uniq_conflict_pair_type",
            )
        ]
        indexes = [
            models.Index(fields=["project", "state", "created_at"]),
            models.Index(fields=["project", "conflict_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.conflict_type}: {self.left_asset_id} vs {self.right_asset_id}"

    def clean(self):
        if self.left_asset_id and self.right_asset_id:
            if self.left_asset_id == self.right_asset_id:
                raise ValidationError("冲突双方不能是同一条知识")
            projects = {self.left_asset.project_id, self.right_asset.project_id, self.project_id}
            if len(projects) > 1:
                raise ValidationError("冲突双方与冲突记录必须属于同一项目（R9）")
        if self.trust_penalty < 0 or self.trust_penalty > 1:
            raise ValidationError("信任折减系数必须落在 0~1")

    def resolve(self, *, actor, resolution: str, note: str = "") -> None:
        if resolution == "pending":
            raise ValidationError("裁决必须给出具体处理方式")
        if resolution not in dict(self.RESOLUTION_CHOICES):
            raise ValidationError(f"未知的裁决方式 {resolution!r}")
        if self.state == "open":
            self.transition_to("resolving", actor=actor, reason="开始裁决")
        previous = self.state
        self.state = "resolved"
        self.resolution = resolution
        self.resolution_note = note
        self.resolved_by = actor
        self.resolved_at = timezone.now()
        self.save(
            update_fields=[
                "state", "resolution", "resolution_note",
                "resolved_by", "resolved_at", "updated_at",
            ]
        )
        KnowledgeAuditLog.record(
            project_id=self.project_id, actor=actor, action="conflict_resolved", entity=self,
            from_state=previous, to_state="resolved", reason=note or resolution,
        )


# ------------------------------------------------------------------- 7. 索引投影


class IndexProjection(StateMachineMixin):
    """Qdrant/图谱投影一致性（R8 / design.md §4.2、§10）。

    `projection_version` 是换 embedding / 换分块器时的版本位，alias 双缓冲据此切换。
    """

    INDEX_TYPE_CHOICES = [
        ("qdrant_dense", "Qdrant 稠密向量"),
        ("qdrant_sparse", "Qdrant 稀疏向量"),
        ("document_graph", "文档图谱"),
        ("keyword", "关键词索引"),
    ]
    STATE_CHOICES = [
        ("pending", "待写入"),
        ("writing", "写入中"),
        ("ready", "已就绪"),
        ("failed", "写入失败"),
        ("stale", "已过期"),
        ("rolled_back", "已回滚"),
        ("removed", "已回收"),
    ]
    STATE_TRANSITIONS = {
        "pending": {"writing", "removed"},
        "writing": {"ready", "failed"},
        "ready": {"stale", "rolled_back", "writing", "removed"},
        "stale": {"writing", "rolled_back", "ready", "removed"},
        "failed": {"writing", "removed"},
        "rolled_back": {"writing", "removed"},
        "removed": set(),
    }
    STATE_FIELD = "state"
    STATE_LABEL = "索引投影状态"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="index_projections"
    )
    version = models.ForeignKey(
        KnowledgeVersion, on_delete=models.CASCADE, related_name="projections"
    )
    index_type = models.CharField(max_length=32, choices=INDEX_TYPE_CHOICES, db_index=True)
    projection_version = models.CharField(
        max_length=100, db_index=True,
        help_text="投影代数：分块器/embedding 模型版本，alias 切换的比较基准",
    )
    collection_name = models.CharField(max_length=255, blank=True)
    vector_id = models.CharField(max_length=128, blank=True, db_index=True)
    checksum = models.CharField(max_length=64, blank=True)
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default="pending", db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "index_type", "projection_version"],
                name="uniq_projection_version_type_gen",
            )
        ]
        indexes = [
            models.Index(fields=["project", "index_type", "state"]),
            models.Index(fields=["projection_version", "state"]),
        ]

    def __str__(self) -> str:
        return f"{self.index_type}@{self.projection_version} ({self.state})"

    @staticmethod
    def build_idempotency_key(version_id, index_type: str, projection_version: str) -> str:
        return f"{version_id}:{index_type}:{projection_version}"

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = self.build_idempotency_key(
                self.version_id, self.index_type, self.projection_version
            )
        super().save(*args, **kwargs)

    def clean(self):
        if self.version_id and self.project_id:
            if self.version.project_id != self.project_id:
                raise ValidationError("投影与知识版本必须属于同一项目")
        if not self.projection_version:
            raise ValidationError("投影必须标明 projection_version，否则无法做 alias 切换与回滚")

    def mark_ready(self, *, checksum: str = "", collection_name: str = "") -> None:
        _check_transition(self.STATE_TRANSITIONS, self.state, "ready", self.STATE_LABEL)
        self.state = "ready"
        self.attempts += 1
        if checksum:
            self.checksum = checksum
        if collection_name:
            self.collection_name = collection_name
        self.completed_at = timezone.now()
        self.last_error = ""
        self.save(
            update_fields=[
                "state", "attempts", "checksum", "collection_name",
                "completed_at", "last_error", "updated_at",
            ]
        )

    def mark_failed(self, error: str) -> None:
        self.state = "failed"
        self.attempts += 1
        self.last_error = error[:2000]
        self.save(update_fields=["state", "attempts", "last_error", "updated_at"])

    def mark_rolled_back(self) -> None:
        _check_transition(self.STATE_TRANSITIONS, self.state, "rolled_back", self.STATE_LABEL)
        self.state = "rolled_back"
        self.save(update_fields=["state", "updated_at"])


class IndexProjectionOutbox(models.Model):
    """索引投影写入 Outbox（R8 / design.md §4.2）。

    每条记录对应一个向量点写入/删除操作；Celery 任务按批次消费，
    用 `idempotency_key` 保证同一 projection + vector_id 只生效一次。
    """

    OPERATION_CHOICES = [
        ("upsert", "写入/更新"),
        ("delete", "删除"),
    ]
    STATE_CHOICES = [
        ("pending", "待处理"),
        ("processing", "处理中"),
        ("done", "已完成"),
        ("failed", "失败"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    projection = models.ForeignKey(
        IndexProjection, on_delete=models.CASCADE, related_name="outbox"
    )
    vector_id = models.CharField(max_length=128, db_index=True)
    operation = models.CharField(max_length=16, choices=OPERATION_CHOICES, db_index=True)
    payload = models.JSONField(default=dict, blank=True, help_text="向量、payload、稀疏向量等")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="pending", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["projection", "state", "created_at"]),
            models.Index(fields=["projection", "vector_id", "-created_at"]),
        ]

    @staticmethod
    def build_idempotency_key(projection_id, vector_id: str) -> str:
        return f"{projection_id}:{vector_id}"

    def save(self, *args, **kwargs):
        if not self.idempotency_key:
            self.idempotency_key = self.build_idempotency_key(
                self.projection_id, self.vector_id
            )
        super().save(*args, **kwargs)

    def mark_done(self):
        self.state = "done"
        self.attempts += 1
        self.last_error = ""
        self.save(update_fields=["state", "attempts", "last_error", "updated_at"])

    def mark_failed(self, error: str):
        self.state = "failed"
        self.attempts += 1
        self.last_error = error[:2000]
        self.save(update_fields=["state", "attempts", "last_error", "updated_at"])


# ------------------------------------------------------------------- 8. 审计日志


class KnowledgeAuditLog(models.Model):
    """状态流转审计（R9：操作人、理由、前后版本、时间）。

    design.md §4.1 的实体表未单列此表，但 R9 与任务 4 明确要求审计；
    状态机的每次流转都要能回答「谁、为什么、从什么变成什么」。
    """

    ACTION_CHOICES = [
        ("create", "创建"),
        ("transition", "状态流转"),
        ("publish", "发布"),
        ("rollback", "回滚"),
        ("conflict_resolved", "冲突裁决"),
        ("acl_change", "权限变更"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="knowledge_audit_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_audit_logs",
    )
    actor_type = models.CharField(
        max_length=20, choices=ACTOR_TYPE_CHOICES, default="user"
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES, db_index=True)
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.CharField(max_length=64, db_index=True)
    from_state = models.CharField(max_length=32, blank=True)
    to_state = models.CharField(max_length=32, blank=True)
    before_version = models.PositiveIntegerField(null=True, blank=True)
    after_version = models.PositiveIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["entity_type", "entity_id", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}:{self.entity_id}"

    @classmethod
    def record(
        cls, *, project_id, action, entity, actor=None, from_state: str = "",
        to_state: str = "", reason: str = "", before_version=None,
        after_version=None, detail=None,
    ):
        if not project_id:
            return None
        return cls.objects.create(
            project_id=project_id,
            actor=actor if (actor and getattr(actor, "pk", None)) else None,
            actor_type="user" if (actor and getattr(actor, "pk", None)) else "system",
            action=action,
            entity_type=entity.__class__.__name__,
            entity_id=str(entity.pk),
            from_state=from_state or "",
            to_state=to_state or "",
            before_version=before_version,
            after_version=after_version,
            reason=reason or "",
            detail=detail or {},
        )


# ------------------------------------------------------------------- 9. 评测集


