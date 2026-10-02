"""T09：能力评测门禁的**不可变**快照。

为什么单独建模型而不是继续往 ``CapabilityRelease.gate_report`` 里塞：

1. 门禁结论是审批的证据。审批必须能被追溯"当时依据的是哪一份指标"，因此
   快照必须能**多份并存**（每次重跑评测产生新快照），而不是被后续评测覆盖。
2. ``gate_report`` 是 Release 自身的可变字段，任何一次 ``release.save()`` 都可能
   把它改掉；把它当作审批证据链的一环，等于让被审计对象自己保管证据。
3. 快照落库后**禁止修改**（见 ``save()``），这是「门禁快照不可变」的落点。

分区口径说明（与需求 §R7「金标、回归、新鲜、挑战、隐藏集执行影子评测」对齐）：

- ``gold`` / ``regression`` / ``fresh`` / ``challenge`` / ``hidden`` 是 ``EvaluationCase.split``
  的五个取值，构成"分区完整性"检查的必需集合。
- ``shadow``（影子）**不是** case 分区，而是"候选与基线在同一批分区上可比对"的关系，
  因此它作为 ``partitions`` 里的一个**派生项**记录（``baseline_comparable``），
  而不是要求存在 ``split="shadow"`` 的样本。若强行把 shadow 设成 split 取值，
  会让 ``run_full_race``（强制五分区齐全）与新分区口径互相打架。
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class EvaluationGateSnapshot(models.Model):
    """一次门禁判定的完整、不可变记录。"""

    KIND_CHOICES = [
        ("partition", "分区完整性"),
        ("baseline", "基线可比性"),
        ("hard_gate", "硬门禁"),
        ("full", "完整门禁"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="evaluation_gate_snapshots",
    )
    release = models.ForeignKey(
        "knowledge_evolution.CapabilityRelease", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gate_snapshots",
    )
    #: 直接按 Skill 版本查门禁：复合能力没有 SkillVersion，此处留空。
    skill_version = models.ForeignKey(
        "skills.SkillVersion", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gate_snapshots",
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default="full", db_index=True)
    suite = models.ForeignKey(
        "knowledge_evolution.EvaluationSuite", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gate_snapshots",
    )
    gold_dataset_version = models.ForeignKey(
        "knowledge_evolution.GoldDatasetVersion", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gate_snapshots",
    )
    baseline_run = models.ForeignKey(
        "knowledge_evolution.EvaluationRun", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="baseline_gate_snapshots",
    )
    candidate_run = models.ForeignKey(
        "knowledge_evolution.EvaluationRun", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="candidate_gate_snapshots",
    )
    #: 分区明细：``{"gold": {"cases": 12, "results": 12}, "shadow": {"baseline_comparable": true}}``
    partitions = models.JSONField(default=dict, blank=True)
    #: 统一指标：质量、漏报、误报、Token、耗时、稳定性。
    metrics = models.JSONField(default=dict, blank=True)
    #: 逐条硬门禁结论：``[{"code": ..., "ok": bool, "detail": str, "value": ..., "limit": ...}]``
    checks = models.JSONField(default=list, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    passed = models.BooleanField(default=False, db_index=True)
    #: 规范化内容哈希：同一组输入产生的门禁结论稳定复现，用于幂等与"快照是否变化"判定。
    content_hash = models.CharField(max_length=64, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_gate_snapshots",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # 同一候选评测在同一个 kind 下的同一份结论只落一条：重跑评测若指标未变，
            # 不产生重复证据；指标变了 content_hash 变化，自然落新快照。
            models.UniqueConstraint(
                fields=["candidate_run", "kind", "content_hash"],
                name="uniq_gate_snapshot_run_kind_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "passed", "created_at"], name="ke_gate_proj_pass_idx"),
        ]

    def __str__(self) -> str:
        state = "通过" if self.passed else "未通过"
        return f"{self.get_kind_display()}({state})@{self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        """快照一经落库即不可修改。

        审计证据的特性：能被改写就不算证据。若确实需要修正结论，正确做法是
        重新跑一次门禁生成新快照，而不是原地改历史。
        """
        if not self._state.adding and self.pk:
            raise ValidationError("门禁快照不可修改，请重新执行门禁生成新快照")
        super().save(*args, **kwargs)

    @property
    def failed_checks(self) -> list:
        return [item for item in (self.checks or []) if not item.get("ok")]
