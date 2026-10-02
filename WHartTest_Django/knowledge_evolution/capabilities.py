import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from projects.models import Project

from .capability_models import (
    CapabilityDefinition,
    CapabilityRelease,
    PromotionDecision,
    ReleaseObservation,
)
from .evaluation import EvaluationEngine
from .knowledge_models import KnowledgeAuditLog


#: 发布状态机的合法边。
#:
#: 与设计文档第 7 节的状态图对齐，并补上平台既有灰度流程实际使用的两条边
#: （见 ``approve_for_canary`` / ``complete_canary``）：
#:
#: - ``awaiting_approval -> shadow``：负责人批准后进入**灰度观察**再正式激活。
#: - ``shadow -> active``：灰度观察窗口全部健康，完成灰度并激活。
#:
#: 因此 ``shadow`` 在本平台承载两段语义——"静态校验通过后的影子评测"与
#: "审批后的灰度观察"。两条入边都合法，是历史实现与设计目标的并集，不能只留一条，
#: 否则要么破坏既有灰度链路，要么让新校验流程无法落地。
#:
#: ``quarantined`` 需要人工处置，不允许自动流出；``rolled_back`` 为终态。
ALLOWED_RELEASE_TRANSITIONS = {
    "draft": {"validating", "quarantined"},
    "validating": {"shadow", "rejected", "quarantined"},
    "shadow": {"awaiting_approval", "active", "rejected", "quarantined"},
    "awaiting_approval": {"active", "shadow", "rejected", "quarantined"},
    "active": {"retired", "rolled_back", "quarantined"},
    "retired": {"active", "quarantined"},
    "rejected": {"quarantined"},
    "rolled_back": set(),
    "quarantined": set(),
}


def assert_can_transition(current: str, target: str) -> None:
    """校验一次状态跳转是否合法（只允许走状态图中的直接边）。"""
    if current == target:
        return
    allowed = ALLOWED_RELEASE_TRANSITIONS.get(current)
    if allowed is None:
        raise ValidationError(f"未知的发布状态：{current}")
    if target not in allowed:
        raise ValidationError(f"非法状态跳转：{current} -> {target}")


def can_reach(current: str, target: str) -> bool:
    """沿合法边做可达性判断，用于"一条链路推进到目标态"的复合校验。"""
    if current == target:
        return True
    seen = {current}
    frontier = [current]
    while frontier:
        node = frontier.pop()
        for successor in ALLOWED_RELEASE_TRANSITIONS.get(node, ()):  # 未知状态视为无边
            if successor == target:
                return True
            if successor not in seen:
                seen.add(successor)
                frontier.append(successor)
    return False


def assert_can_reach(current: str, target: str) -> None:
    """校验从当前状态出发能否到达目标状态（允许中间经过若干合法边）。

    典型用途：影子评测通过后要把 ``draft`` 一路推进到 ``awaiting_approval``，
    只要路径存在就放行；而从 ``rejected``/``rolled_back`` 复活则一律拒绝。
    """
    if not can_reach(current, target):
        raise ValidationError(f"不允许的状态流转：{current} 无法到达 {target}")


class CapabilityReleaseService:
    @staticmethod
    def create(*, project, kind, name, version, config, actor=None, candidate=None):
        canonical = json.dumps(config or {}, ensure_ascii=False, sort_keys=True)
        return CapabilityRelease.objects.create(
            project=project, kind=kind, name=name, version=version, config=config or {},
            artifact_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            candidate=candidate, created_by=actor,
        )

    @staticmethod
    def evaluate_shadow(release, baseline_run, candidate_run, *, min_mean_diff=0.0,
                        max_latency_regression=0.2, max_token_regression=0.2,
                        min_sample_count=1, max_p_value=0.05,
                        require_significance=False):
        if baseline_run.suite_id != candidate_run.suite_id:
            raise ValidationError("基线与候选必须使用同一评测集")
        comparisons = {
            level: EvaluationEngine.compare_runs(
                baseline_run.results.filter(status="completed"),
                candidate_run.results.filter(status="completed"), level,
            ) for level in ("l0", "l1", "l2", "l3")
        }
        baseline_cost, candidate_cost = baseline_run.cost_summary or {}, candidate_run.cost_summary or {}
        def regression(key):
            base = float(baseline_cost.get(key) or 0)
            candidate = float(candidate_cost.get(key) or 0)
            return 0.0 if base <= 0 else (candidate - base) / base
        baseline_count = baseline_run.results.filter(status="completed").count()
        candidate_count = candidate_run.results.filter(status="completed").count()
        hard_gates = {
            # 兼容旧的非金标影子评测；一旦任一运行声明了金标，
            # 则必须严格比对冻结版本和回放哈希。
            "same_frozen_gold_version": (
                not baseline_run.gold_dataset_version_id
                and not candidate_run.gold_dataset_version_id
            ) or bool(
                baseline_run.gold_dataset_version_id
                and baseline_run.gold_dataset_version_id == candidate_run.gold_dataset_version_id
                and baseline_run.replay_hash
                and baseline_run.replay_hash == candidate_run.replay_hash
            ),
            "same_sample_count": baseline_count == candidate_count,
            "minimum_sample_count": candidate_count >= min_sample_count,
            **{
                f"no_{level}_regression": (comparisons[level]["mean_diff"] or 0) >= min_mean_diff
                for level in ("l0", "l1", "l2", "l3")
            },
            "latency_within_budget": regression("total_latency_ms") <= max_latency_regression,
            "tokens_within_budget": regression("total_tokens") <= max_token_regression,
        }
        if require_significance:
            improved_levels = [
                item for item in comparisons.values()
                if (item.get("mean_diff") or 0) > 0
            ]
            hard_gates["improvement_statistically_significant"] = bool(improved_levels) and all(
                item.get("p_value") is not None and item["p_value"] <= max_p_value
                for item in improved_levels
            )
        passed = all(hard_gates.values())
        target_state = "awaiting_approval" if passed else "rejected"
        # 条件：当前状态无法沿状态机到达目标态（例如已被隔离或已回滚）；
        # 动作：拒绝；结果：不让评测结果把一个非法状态"救回来"。
        assert_can_reach(release.state, target_state)
        release.baseline_run = baseline_run
        release.candidate_run = candidate_run
        release.gate_report = {
            "passed": passed, "hard_gates": hard_gates, "comparisons": comparisons,
            "cost_regression": {
                "latency": regression("total_latency_ms"), "tokens": regression("total_tokens"),
            },
        }
        release.state = target_state
        release.save(update_fields=["baseline_run", "candidate_run", "gate_report", "state", "updated_at"])
        return release.gate_report

    # ------------------------------------------------------------------
    # 活跃指针维护
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_bound_definition(release):
        """定位 Release 对应的能力定义。

        优先取 ``config["capability_definition_id"]``（显式绑定，最可靠），
        否则按 ``(project, kind, name)`` 回落匹配。
        """
        definition_id = (release.config or {}).get("capability_definition_id")
        queryset = CapabilityDefinition.objects.filter(project_id=release.project_id)
        if definition_id:
            explicit = queryset.filter(pk=definition_id).first()
            if explicit is not None:
                return explicit
        return queryset.filter(kind=release.kind, name=release.name).first()

    @staticmethod
    def refresh_active_pointers(release):
        """把冗余的"当前活跃版本"指针刷新到与发布状态一致。

        ``CapabilityRelease.state`` 是唯一真值；``Skill.active_version`` 与
        ``CapabilityDefinition.active_release`` 只是为运行时快速解析准备的缓存。
        它们必须在状态变更的**同一事务内**刷新，否则运行时可能解析到已退役、
        已隔离或已回滚的版本。
        """
        # 延迟导入避免 skills 与 knowledge_evolution 在模块加载期互相依赖。
        from skills.models import Skill, SkillVersion

        if release.kind == "skill":
            version = SkillVersion.objects.filter(release=release).first()
            if version is not None:
                skill = Skill.objects.select_for_update().get(pk=version.skill_id)
                active_version = SkillVersion.objects.filter(
                    skill_id=skill.id, release__state="active"
                ).order_by("-created_at").first()
                target_version_id = active_version.id if active_version else None
                if skill.active_version_id != target_version_id:
                    skill.active_version = active_version
                    update_fields = ["active_version", "updated_at"]
                    # 兼容旧运行时：老代码通过 ``Skill.skill_path`` 找脚本目录。
                    # 版本化之后该字段指向"当前活跃版本的不可变目录"；没有活跃版本时
                    # 清空，这样连旧的执行路径也会因为找不到目录而拒绝执行（R13）。
                    new_path = active_version.package_path if active_version else ""
                    if skill.skill_path != new_path:
                        skill.skill_path = new_path
                        update_fields.append("skill_path")
                    skill.save(update_fields=update_fields)
                if skill.capability_id:
                    definition = CapabilityDefinition.objects.select_for_update().get(
                        pk=skill.capability_id
                    )
                    definition.active_release = CapabilityRelease.objects.filter(
                        project_id=release.project_id, kind="skill",
                        name=release.name, state="active",
                    ).first()
                    definition.save(update_fields=["active_release", "updated_at"])
                return

        definition = CapabilityReleaseService.resolve_bound_definition(release)
        if definition is not None:
            definition = CapabilityDefinition.objects.select_for_update().get(pk=definition.pk)
            definition.active_release = CapabilityRelease.objects.filter(
                project_id=release.project_id, kind=release.kind,
                name=release.name, state="active",
            ).first()
            definition.save(update_fields=["active_release", "updated_at"])

    # ------------------------------------------------------------------
    # 状态迁移
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def promote(release, *, actor, reason=""):
        """审批通过后原子激活候选版本。

        并发安全由两层保证：项目行锁把所有激活操作串行化，数据库的部分唯一约束
        ``uniq_active_release_per_target`` 作为兜底，确保同一目标最多一个 active。
        """
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        if release.state != "awaiting_approval" or not release.gate_report.get("passed"):
            raise ValidationError("候选尚未通过影子门禁")
        # 补上状态图校验：本方法的其它兄弟（rollback/quarantine/approve_for_canary/
        # submit_for_approval）都会校验合法边，只有 promote 之前没校验。
        # 后果是隔离等终态只要被任何路径写回 awaiting_approval，就能被激活上线。
        assert_can_transition(release.state, "active")
        # 负责人批准的必须是"当前这一份"评测证据（T13/R7）。
        # 候选在提交审批后被重新评测时，这里会拒绝激活并要求重新审批。
        CapabilityReleaseService.assert_approval_evidence_current(release)
        # 退役范围必须按 (project, kind, name) 而不是 (project, kind)：
        # 同一项目下多个 Skill 各自有独立发布线，粒度太粗会把别人的活跃版本误退役。
        current = CapabilityRelease.objects.filter(
            project_id=release.project_id, kind=release.kind,
            name=release.name, state="active",
        ).exclude(pk=release.pk).first()
        if current is not None:
            current.state = "retired"
            current.save(update_fields=["state", "updated_at"])
        previous_state = release.state
        release.previous_release = current or release.previous_release
        release.state = "active"
        release.approved_by = actor
        release.approved_at = timezone.now()
        release.activated_at = timezone.now()
        release.save()
        CapabilityReleaseService.refresh_active_pointers(release)
        PromotionDecision.objects.create(release=release, decision="approved", gate_snapshot=release.gate_report, reason=reason, actor=actor)
        KnowledgeAuditLog.record(
            project_id=release.project_id, actor=actor, action="approve",
            entity=release, from_state=previous_state, to_state="active",
            reason=reason or "负责人审批通过并原子激活",
            detail={
                "snapshot_hash": release.approval_snapshot_hash,
                "retired_release_id": str(current.id) if current else "",
                "rollback_target": str(current.id) if current else "",
            },
        )
        return release

    @staticmethod
    @transaction.atomic
    def rollback(release, *, actor, reason=""):
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        if release.state != "active":
            raise ValidationError("只有生产生效版本可以回滚")
        previous = release.previous_release
        release.state = "rolled_back"
        release.save(update_fields=["state", "updated_at"])
        if previous is not None:
            previous.refresh_from_db()
            # retired -> active 是状态图允许的复活边；若上一版本已被隔离，则拒绝回滚，
            # 避免把风险版本重新推回生产。
            assert_can_transition(previous.state, "active")
            previous.state = "active"
            previous.activated_at = timezone.now()
            previous.save(update_fields=["state", "activated_at", "updated_at"])
        CapabilityReleaseService.refresh_active_pointers(release)
        PromotionDecision.objects.create(release=release, decision="rollback", gate_snapshot=release.gate_report, reason=reason, actor=actor)
        KnowledgeAuditLog.record(
            project_id=release.project_id, actor=actor, action="rollback",
            entity=release, from_state="active", to_state="rolled_back",
            reason=reason or "生产观察超阈值或人工回滚",
            detail={
                "restored_release_id": str(previous.id) if previous else "",
                "restored_state": previous.state if previous else "",
                "observation_state": release.observation_state,
            },
        )
        return previous

    @staticmethod
    @transaction.atomic
    def quarantine(release, *, actor, reason):
        """紧急隔离：立即阻断运行时加载，并刷新活跃指针。

        与 ``rejected`` 的区别是隔离面向**已入库/已生效**的版本，属于安全事件，
        必须填写原因并留痕。
        """
        if not (reason or "").strip():
            raise ValidationError("隔离必须填写原因")
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        assert_can_transition(release.state, "quarantined")
        release.state = "quarantined"
        release.save(update_fields=["state", "updated_at"])
        CapabilityReleaseService.refresh_active_pointers(release)
        PromotionDecision.objects.create(
            release=release, decision="quarantine",
            gate_snapshot=release.gate_report, reason=reason.strip(), actor=actor,
        )
        return release

    @staticmethod
    @transaction.atomic
    def approve_for_canary(release, *, actor, reason=""):
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        if release.state != "awaiting_approval" or not release.gate_report.get("passed"):
            raise ValidationError("候选尚未通过完整门禁")
        assert_can_transition(release.state, "shadow")
        current = CapabilityRelease.objects.filter(
            project_id=release.project_id, kind=release.kind,
            name=release.name, state="active",
        ).exclude(pk=release.pk).first()
        release.previous_release = current
        release.state = "shadow"
        release.approved_by = actor
        release.approved_at = timezone.now()
        release.save(update_fields=[
            "previous_release", "state", "approved_by", "approved_at", "updated_at",
        ])
        PromotionDecision.objects.create(
            release=release, decision="approved", gate_snapshot=release.gate_report,
            reason=reason or "负责人批准进入灰度", actor=actor,
        )
        return release

    # ------------------------------------------------------------------
    # T13：提交审批 / 批准 / 驳回 / 生产观察与自动回滚
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def submit_for_approval(release, *, actor, reason="", gate_snapshot=None, kind: str = "full"):
        """把通过硬门禁的候选置为 ``awaiting_approval``——**而不是**直接激活。

        幂等：同一份门禁快照重复提交，直接返回当前状态，不重复写决策记录。
        这样前端连点两次不会产生两条审批历史。
        """
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        assert_can_transition(release.state, "awaiting_approval")

        snapshot = gate_snapshot
        if snapshot is None:
            from .evaluation_gates import EvaluationGateService

            snapshot = EvaluationGateService.assert_can_submit_approval(release, kind=kind)

        snapshot_hash = getattr(snapshot, "content_hash", "") or str(
            (snapshot or {}).get("content_hash", "")
        )
        if release.state == "awaiting_approval" and release.approval_snapshot_hash == snapshot_hash:
            return release

        previous_state = release.state
        release.gate_report = {
            "passed": bool(getattr(snapshot, "passed", True)),
            "snapshot_id": str(getattr(snapshot, "id", "") or ""),
            "content_hash": snapshot_hash,
            "checks": getattr(snapshot, "checks", []) or [],
            "metrics": getattr(snapshot, "metrics", {}) or {},
            "partitions": getattr(snapshot, "partitions", {}) or {},
        }
        release.approval_snapshot_hash = snapshot_hash
        release.state = "awaiting_approval"
        release.save(update_fields=[
            "gate_report", "approval_snapshot_hash", "state", "updated_at",
        ])
        PromotionDecision.objects.create(
            release=release, decision="approved", gate_snapshot=release.gate_report,
            reason=reason or "门禁通过，提交测试负责人审批", actor=actor,
        )
        KnowledgeAuditLog.record(
            project_id=release.project_id, actor=actor, action="submit_approval",
            entity=release, from_state=previous_state, to_state="awaiting_approval",
            reason=reason or "门禁通过，提交测试负责人审批",
            detail={
                "snapshot_id": release.gate_report.get("snapshot_id", ""),
                "snapshot_hash": snapshot_hash,
            },
        )
        return release

    @staticmethod
    @transaction.atomic
    def assert_approval_evidence_current(release) -> None:
        """激活前重新核对：负责人批准的仍是"当前这一份"评测证据。

        只在提交审批时写过快照哈希的版本上生效。手工构造的 / 历史遗留的发布
        （没有快照哈希）退回到原有的 ``gate_report.passed`` 判定——
        这是兼容路径，不是放宽：它对应"该版本从未走过 T09 门禁"的存量数据。
        """
        if not release.approval_snapshot_hash:
            return
        from .evaluation_gates import EvaluationGateService

        latest = EvaluationGateService.latest_snapshot(release)
        if latest is None:
            raise ValidationError("审批时引用的门禁快照已不存在，必须重新执行门禁并重新审批")
        if latest.content_hash != release.approval_snapshot_hash:
            raise ValidationError(
                "候选在审批后被重新评测，评测证据已变化，必须重新提交审批"
            )

    @staticmethod
    @transaction.atomic
    def reject(release, *, actor, reason):
        """负责人驳回候选。原因必填：驳回而不留原因，执行人员无法知道要改什么。"""
        if not (reason or "").strip():
            raise ValidationError("驳回必须填写原因")
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        assert_can_transition(release.state, "rejected")
        previous_state = release.state
        release.state = "rejected"
        release.save(update_fields=["state", "updated_at"])
        CapabilityReleaseService.refresh_active_pointers(release)
        PromotionDecision.objects.create(
            release=release, decision="rejected", gate_snapshot=release.gate_report,
            reason=reason.strip(), actor=actor,
        )
        KnowledgeAuditLog.record(
            project_id=release.project_id, actor=actor, action="reject",
            entity=release, from_state=previous_state, to_state="rejected",
            reason=reason.strip(),
        )
        return release

    @staticmethod
    @transaction.atomic
    def observe(
        release, *, window_key, metrics, actor=None, thresholds=None,
        auto_rollback: bool = True,
    ) -> dict:
        """记录一个观察窗口，并在超阈值时自动回滚。

        与 ``record_observation`` 的分工：``record_observation`` 是判定与落库，
        ``observe`` 是"判定 + 处置 + 返回可展示结论"。生产链路调 ``observe``。

        「回滚失败」的处理是刻意的（设计 §11）：回滚本身失败时不能让能力留在
        active 上继续放量，因此转 ``quarantined`` 阻断新任务，并记一条高优先级审计。
        """
        release.refresh_from_db()
        previous_state = release.state
        observation = CapabilityReleaseService.record_observation(
            release, window_key=window_key, metrics=metrics,
            actor=actor, thresholds=thresholds, auto_handle=False,
        )
        release.refresh_from_db()

        result = {
            "observation_id": str(observation.id),
            "window_key": observation.window_key,
            "status": observation.status,
            "breaches": observation.breaches,
            "previous_state": previous_state,
            "state": release.state,
            "rolled_back": False,
            "quarantined": False,
            "rollback_target": None,
            "alert": "",
        }
        if observation.status != "breached":
            release.observation_state = "healthy"
            release.save(update_fields=["observation_state", "updated_at"])
            return result

        breached = "、".join(item["metric"] for item in observation.breaches)
        result["alert"] = f"{release.kind}:{release.name}@{release.version} 指标超阈值：{breached}"
        release.observation_state = "breached"
        release.save(update_fields=["observation_state", "updated_at"])

        if previous_state == "active" and auto_rollback:
            try:
                restored = CapabilityReleaseService.rollback(
                    release, actor=actor, reason=result["alert"],
                )
                result["rolled_back"] = True
                result["rollback_target"] = str(restored.id) if restored else None
                result["state"] = "rolled_back"
            except Exception as exc:  # noqa: BLE001 - 回滚失败的兜底必须兜住一切
                # 回滚失败绝不能让版本继续 active：转隔离，阻断新任务，升级审计。
                release.refresh_from_db()
                if release.state != "quarantined":
                    release.state = "quarantined"
                    release.save(update_fields=["state", "updated_at"])
                    CapabilityReleaseService.refresh_active_pointers(release)
                result["quarantined"] = True
                result["state"] = "quarantined"
                result["alert"] += f"；且回滚失败（{exc}），已紧急隔离阻断新任务"
                KnowledgeAuditLog.record(
                    project_id=release.project_id, actor=actor, action="quarantine",
                    entity=release, from_state=previous_state, to_state="quarantined",
                    reason=result["alert"], detail={"rollback_failed": True},
                )
        else:
            result["state"] = release.state
        return result

    @staticmethod
    @transaction.atomic
    def reject_for_observations(release, *, actor, reason=""):
        """灰度期观察超阈值的处置：直接拒绝该候选（尚未进入生产）。"""
        release.refresh_from_db()
        assert_can_transition(release.state, "rejected")
        release.state = "rejected"
        release.save(update_fields=["state", "updated_at"])
        PromotionDecision.objects.create(
            release=release, decision="rejected", gate_snapshot=release.gate_report,
            reason=reason or "灰度观察窗口超阈值", actor=actor,
        )
        return release

    @staticmethod
    def approval_view(release) -> dict:
        """给前端审批面板用的完整状态：门禁、缺失条件、可否操作。

        "可否操作"与后端校验共用同一批判据（``missing_conditions``），
        因此前端禁用按钮的原因与后端报错的原因不会不一致。
        """
        from .evaluation_gates import EvaluationGateService

        missing = EvaluationGateService.missing_conditions(release)
        snapshot = EvaluationGateService.latest_snapshot(release)
        evidence_stale = False
        if release.approval_snapshot_hash and snapshot is not None:
            evidence_stale = snapshot.content_hash != release.approval_snapshot_hash
        return {
            "release_id": str(release.id),
            "state": release.state,
            "state_label": dict(CapabilityRelease.STATE_CHOICES).get(release.state, release.state),
            "version": release.version,
            "approval_snapshot_hash": release.approval_snapshot_hash,
            "latest_snapshot_hash": snapshot.content_hash if snapshot else "",
            "evidence_stale": evidence_stale,
            "observation_state": release.observation_state,
            "missing_conditions": missing,
            "can_submit_approval": release.state == "shadow" and not missing,
            "can_activate": (
                release.state == "awaiting_approval"
                and bool(release.gate_report.get("passed"))
                and not evidence_stale
            ),
            "gate_report": release.gate_report,
            "decisions": [
                {
                    "decision": item.decision, "reason": item.reason,
                    "actor": item.actor.username if item.actor else "",
                    "created_at": item.created_at.isoformat(),
                }
                for item in release.decisions.all()[:20]
            ],
            "observations": [
                {
                    "window_key": item.window_key, "status": item.status,
                    "breaches": item.breaches, "metrics": item.metrics,
                    "created_at": item.created_at.isoformat(),
                }
                for item in release.observations.all()[:10]
            ],
        }

    @staticmethod
    @transaction.atomic
    def record_observation(release, *, window_key, metrics, actor=None, thresholds=None,
                           auto_handle: bool = True):
        """判定一个观察窗口是否超阈值，并落库。

        ``auto_handle=False`` 只做**判定**、不做处置。``observe`` 需要这个模式：
        处置里包含"回滚失败要转隔离"这类兜底，必须先拿到判定结果再自己处理；
        若判定函数顺手把回滚也做了，处置层再去回滚就会撞上"只有 active 可回滚"而报错，
        结果把一个正常的回滚误判成"回滚失败"并升级为隔离——这正是本批次踩过的坑。
        """
        if release.state not in {"shadow", "active"}:
            raise ValidationError("只能监控灰度或生产生效版本")
        configured = ((release.config or {}).get("canary") or {}).get("thresholds") or {}
        limits = {
            "max_error_rate": 0.05,
            "max_latency_regression": 0.20,
            "max_token_regression": 0.20,
            "min_l1_score": 0.70,
            **configured,
            **(thresholds or {}),
        }
        values = metrics or {}
        checks = [
            ("error_rate", float(values.get("error_rate", 0)), "max_error_rate", "max"),
            ("latency_regression", float(values.get("latency_regression", 0)), "max_latency_regression", "max"),
            ("token_regression", float(values.get("token_regression", 0)), "max_token_regression", "max"),
            ("l1_score", float(values.get("l1_score", 1)), "min_l1_score", "min"),
        ]
        breaches = []
        for metric, value, threshold_key, direction in checks:
            limit = float(limits[threshold_key])
            breached = value > limit if direction == "max" else value < limit
            if breached:
                breaches.append({"metric": metric, "value": value, "threshold": limit})
        observation, _ = ReleaseObservation.objects.update_or_create(
            release=release, window_key=str(window_key),
            defaults={
                "metrics": values, "thresholds": limits, "breaches": breaches,
                "status": "breached" if breaches else "healthy", "actor": actor,
            },
        )
        if breaches and auto_handle:
            reason = "监控指标超阈值：" + ", ".join(item["metric"] for item in breaches)
            if release.state == "active":
                CapabilityReleaseService.rollback(release, actor=actor, reason=reason)
            else:
                release.state = "rejected"
                release.save(update_fields=["state", "updated_at"])
                PromotionDecision.objects.create(
                    release=release, decision="rejected", gate_snapshot=release.gate_report,
                    reason=reason, actor=actor,
                )
        return observation

    @staticmethod
    @transaction.atomic
    def complete_canary(release, *, actor, reason="", min_observations=None):
        Project.objects.select_for_update().get(pk=release.project_id)
        release.refresh_from_db()
        if release.state != "shadow":
            raise ValidationError("只有灰度中的版本可以完成灰度")
        assert_can_transition(release.state, "active")
        required = int(
            min_observations
            if min_observations is not None
            else ((release.config or {}).get("canary") or {}).get("min_observations", 3)
        )
        observations = release.observations.all()
        if observations.count() < required:
            raise ValidationError(f"灰度观察窗口不足，至少需要 {required} 个")
        if observations.filter(status="breached").exists():
            raise ValidationError("灰度期存在超阈值指标，不得正式激活")
        previous = release.previous_release
        if previous is not None:
            previous.refresh_from_db()
            if previous.state == "active":
                previous.state = "retired"
                previous.save(update_fields=["state", "updated_at"])
        release.state = "active"
        release.activated_at = timezone.now()
        release.save(update_fields=["state", "activated_at", "updated_at"])
        CapabilityReleaseService.refresh_active_pointers(release)
        return release
