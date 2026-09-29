"""任务 4 的模型级回归：状态机、幂等键、ACL、分级有效期、冲突与投影一致性。

设计文档对应关系：design.md §4.1（存储）、§5（加工状态机）、§5.3（分级与有效期）。
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from projects.models import Project

from .models import (
    IndexProjection,
    KnowledgeAsset,
    KnowledgeAuditLog,
    KnowledgeCandidate,
    KnowledgeConflict,
    KnowledgeEvidence,
    KnowledgeVersion,
    SourceSnapshot,
)


class FlywheelModelTestCase(TestCase):
    """公共夹具：两个项目、两个用户。"""

    def setUp(self):
        self.user = User.objects.create_user(username="assets-owner", password="pass")
        self.approver = User.objects.create_user(username="assets-approver", password="pass")
        self.project = Project.objects.create(name="Asset Project", creator=self.user)
        self.other_project = Project.objects.create(name="Other Asset Project", creator=self.user)

    # -------------------------------------------------------------- 构造助手

    def make_snapshot(self, *, project=None, source_id="doc-1", revision="r1",
                      content_hash="h1", authority="internal", **kwargs):
        snap, _ = SourceSnapshot.get_or_create(
            project=project or self.project,
            source_type="document",
            source_id=source_id,
            revision=revision,
            content_hash=content_hash,
            defaults={"authority": authority, **kwargs},
        )
        return snap

    def make_asset(self, *, level="L2", key="payment.idempotency", project=None, **kwargs):
        return KnowledgeAsset.objects.create(
            project=project or self.project,
            asset_type="rule",
            key=key,
            title="支付必须幂等",
            level=level,
            **kwargs,
        )

    def make_version(self, *, asset=None, snapshot=None, version=1, status="pending_eval", **kwargs):
        return KnowledgeVersion.objects.create(
            asset=asset or self.make_asset(),
            version=version,
            content="对象：支付接口；约束：必须幂等；适用域：生产环境",
            content_hash=f"c{version}",
            source_snapshot=snapshot or self.make_snapshot(),
            status=status,
            **kwargs,
        )


class SourceSnapshotTests(FlywheelModelTestCase):
    def test_idempotency_key_blocks_duplicate_snapshot(self):
        self.make_snapshot(source_id="doc-9", revision="r1", content_hash="abc")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceSnapshot.objects.create(
                project=self.project, source_type="document", source_id="doc-9",
                revision="r1", content_hash="abc",
            )

    def test_same_source_with_new_content_hash_is_a_new_snapshot(self):
        first = self.make_snapshot(source_id="doc-9", revision="r1", content_hash="abc")
        second = self.make_snapshot(source_id="doc-9", revision="r1", content_hash="def")
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    def test_state_machine_rejects_skipping_parse(self):
        snapshot = self.make_snapshot()
        self.assertTrue(snapshot.can_transition_to("parsed"))
        self.assertFalse(snapshot.can_transition_to("extracted"))
        with self.assertRaises(ValidationError):
            snapshot.transition_to("extracted")
        self.assertEqual(SourceSnapshot.objects.get(pk=snapshot.pk).status, "captured")

    def test_extracted_is_terminal_but_can_be_superseded(self):
        snapshot = self.make_snapshot()
        snapshot.transition_to("parsed")
        snapshot.transition_to("extracted")
        snapshot.transition_to("superseded")
        self.assertEqual(snapshot.available_transitions(), [])
        with self.assertRaises(ValidationError):
            snapshot.transition_to("parsed")

    def test_l1_gate_recognises_authoritative_sources(self):
        self.assertTrue(self.make_snapshot(source_id="a", authority="official").is_authoritative)
        self.assertTrue(self.make_snapshot(source_id="b", authority="standard").is_authoritative)
        self.assertFalse(self.make_snapshot(source_id="c", authority="internal").is_authoritative)

    def test_acl_visible_with_filters_restricted_snapshots(self):
        public = self.make_snapshot(source_id="pub")
        restricted = self.make_snapshot(source_id="sec", acl_tags=["sec-team"])
        self.assertTrue(public.is_public_to_project)
        self.assertFalse(restricted.is_public_to_project)

        visible_for_outsider = set(
            SourceSnapshot.objects.visible_with([]).values_list("pk", flat=True)
        )
        self.assertIn(public.pk, visible_for_outsider)
        self.assertNotIn(restricted.pk, visible_for_outsider)

        visible_for_insider = set(
            SourceSnapshot.objects.visible_with(["sec-team"]).values_list("pk", flat=True)
        )
        self.assertIn(restricted.pk, visible_for_insider)
        self.assertTrue(restricted.allows(["sec-team"]))
        self.assertFalse(restricted.allows(["other-team"]))

    def test_evidence_queryset_enforces_acl_at_query_time(self):
        version = self.make_version()
        open_snap = self.make_snapshot(source_id="open")
        secret_snap = self.make_snapshot(source_id="secret", acl_tags=["sec-team"])
        KnowledgeEvidence.objects.create(version=version, snapshot=open_snap, relation="supported_by")
        KnowledgeEvidence.objects.create(version=version, snapshot=secret_snap, relation="supported_by")

        outsider = set(
            KnowledgeEvidence.accessible_for([]).values_list("snapshot_id", flat=True)
        )
        self.assertIn(open_snap.pk, outsider)
        self.assertNotIn(secret_snap.pk, outsider)
        insider = set(
            KnowledgeEvidence.accessible_for(["sec-team"]).values_list("snapshot_id", flat=True)
        )
        self.assertIn(secret_snap.pk, insider)


class KnowledgeAssetTests(FlywheelModelTestCase):
    def test_key_is_unique_per_project_and_type(self):
        self.make_asset(key="dup.key")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_asset(key="dup.key")
        # 另一个项目可以复用同一个 key
        self.make_asset(key="dup.key", project=self.other_project)

    def test_review_interval_follows_level(self):
        self.assertEqual(self.make_asset(level="L1", key="l1").review_interval_days, 90)
        self.assertEqual(self.make_asset(level="L2", key="l2").review_interval_days, 180)
        self.assertEqual(self.make_asset(level="L3", key="l3").review_interval_days, 90)

    def test_only_l1_requires_dual_approval(self):
        self.assertTrue(self.make_asset(level="L1", key="l1-only").requires_dual_approval)
        self.assertFalse(self.make_asset(level="L2", key="l2-only").requires_dual_approval)

    def test_next_version_number_increments(self):
        asset = self.make_asset()
        self.assertEqual(asset.next_version_number(), 1)
        self.make_version(asset=asset, version=1)
        self.assertEqual(asset.next_version_number(), 2)


class KnowledgeVersionTests(FlywheelModelTestCase):
    def test_cross_project_source_snapshot_is_rejected(self):
        asset = self.make_asset()
        foreign_snapshot = self.make_snapshot(project=self.other_project, source_id="foreign")
        version = KnowledgeVersion(
            asset=asset, version=1, content="x", content_hash="x",
            source_snapshot=foreign_snapshot,
        )
        with self.assertRaises(ValidationError):
            version.full_clean()

    def test_validity_window_defaults_by_level(self):
        l1 = self.make_version(asset=self.make_asset(level="L1", key="va"))
        l1.set_validity()
        self.assertAlmostEqual(
            (l1.expires_at - l1.valid_from).days, 90, delta=1
        )
        l2 = self.make_version(asset=self.make_asset(level="L2", key="vb"), version=1)
        l2.set_validity()
        self.assertAlmostEqual((l2.expires_at - l2.valid_from).days, 180, delta=1)

    def test_expiry_before_validity_is_rejected(self):
        version = self.make_version()
        version.valid_from = timezone.now()
        version.expires_at = version.valid_from - timezone.timedelta(days=1)
        with self.assertRaises(ValidationError):
            version.full_clean()

    def test_published_without_approver_is_rejected_by_clean(self):
        version = self.make_version(status="published")
        with self.assertRaises(ValidationError):
            version.full_clean()

    def test_cannot_jump_from_pending_eval_straight_to_published(self):
        version = self.make_version()
        with self.assertRaises(ValidationError):
            version.publish(actor=self.user)

    def test_l2_publish_activates_asset_and_writes_audit(self):
        asset = self.make_asset(level="L2")
        version = self.make_version(asset=asset, status="awaiting_approval")
        version.approve(actor=self.user, reason="评测通过")

        version.publish(actor=self.user, reason="评测通过")

        version.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(version.status, "published")
        self.assertIsNotNone(version.expires_at)
        self.assertEqual(asset.current_version_id, version.pk)
        self.assertEqual(asset.status, "active")
        self.assertTrue(
            KnowledgeAuditLog.objects.filter(
                project=self.project, action="publish", entity_id=str(version.pk)
            ).exists()
        )

    def test_l1_publish_requires_second_approver_and_authoritative_evidence(self):
        asset = self.make_asset(level="L1", key="l1.gate")
        weak = self.make_snapshot(source_id="weak", authority="internal")
        version = self.make_version(asset=asset, snapshot=weak, status="awaiting_approval")

        # 双审到位但证据非权威 -> 拒绝
        version.approve(actor=self.user, second_approver=self.approver)
        with self.assertRaises(ValidationError) as ctx:
            version.publish(actor=self.user)
        self.assertIn("权威", str(ctx.exception))

        # 补上权威证据后放行
        strong = self.make_snapshot(source_id="strong", authority="standard")
        KnowledgeEvidence.objects.create(version=version, snapshot=strong, relation="supported_by")
        version.publish(actor=self.user)
        version.refresh_from_db()
        self.assertEqual(version.status, "published")

    def test_l1_dual_approval_must_use_two_distinct_users(self):
        asset = self.make_asset(level="L1", key="l1.dup")
        version = self.make_version(asset=asset, status="awaiting_approval")
        with self.assertRaises(ValidationError):
            version.approve(actor=self.user, second_approver=self.user)

    def test_rollback_requires_reason_and_restores_previous_version(self):
        asset = self.make_asset()
        first = self.make_version(asset=asset, version=1)
        first.approve(actor=self.user)
        first.publish(actor=self.user)

        second = self.make_version(asset=asset, version=2, snapshot=first.source_snapshot)
        second.approve(actor=self.user)
        second.publish(actor=self.user)
        second.previous_version = first
        second.save(update_fields=["previous_version"])

        with self.assertRaises(ValidationError):
            second.rollback(actor=self.user, reason="")

        second.rollback(actor=self.user, reason="线上出现严重反例")
        second.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(second.status, "rolled_back")
        self.assertEqual(asset.current_version_id, first.pk)
        self.assertTrue(
            KnowledgeAuditLog.objects.filter(
                project=self.project, action="rollback", entity_id=str(second.pk)
            ).exists()
        )


class KnowledgeEvidenceTests(FlywheelModelTestCase):
    def test_cross_project_evidence_is_rejected(self):
        version = self.make_version()
        foreign = self.make_snapshot(project=self.other_project, source_id="f")
        evidence = KnowledgeEvidence(version=version, snapshot=foreign, relation="supported_by")
        with self.assertRaises(ValidationError):
            evidence.full_clean()

    def test_location_hash_is_derived_and_dedupes_same_location(self):
        version = self.make_version()
        snapshot = self.make_snapshot(source_id="loc")
        first = KnowledgeEvidence.objects.create(
            version=version, snapshot=snapshot, relation="supported_by",
            location={"page": 3, "section": "2.1"},
        )
        self.assertTrue(first.location_hash)

        with self.assertRaises(IntegrityError), transaction.atomic():
            KnowledgeEvidence.objects.create(
                version=version, snapshot=snapshot, relation="supported_by",
                location={"page": 3, "section": "2.1"},
            )
        # 换位置就可以共存
        second = KnowledgeEvidence.objects.create(
            version=version, snapshot=snapshot, relation="supported_by",
            location={"page": 9},
        )
        self.assertNotEqual(first.location_hash, second.location_hash)

    def test_negative_weight_is_rejected(self):
        version = self.make_version()
        evidence = KnowledgeEvidence(
            version=version, snapshot=self.make_snapshot(source_id="w"),
            relation="supported_by", weight=-1.0,
        )
        with self.assertRaises(ValidationError):
            evidence.full_clean()


class KnowledgeCandidateTests(FlywheelModelTestCase):
    def test_dedup_key_is_unique(self):
        key = KnowledgeCandidate.build_dedup_key(self.project.pk, "rule", "同一段内容")
        KnowledgeCandidate.objects.create(project=self.project, kind="rule", dedup_key=key)
        with self.assertRaises(IntegrityError), transaction.atomic():
            KnowledgeCandidate.objects.create(project=self.project, kind="rule", dedup_key=key)

    def test_same_content_across_projects_gets_distinct_dedup_keys(self):
        a = KnowledgeCandidate.build_dedup_key(self.project.pk, "rule", "同一段内容")
        b = KnowledgeCandidate.build_dedup_key(self.other_project.pk, "rule", "同一段内容")
        self.assertNotEqual(a, b)

    def test_accept_requires_a_landing_asset(self):
        candidate = KnowledgeCandidate.objects.create(
            project=self.project, kind="rule", dedup_key="c1", state="awaiting_approval"
        )
        with self.assertRaises(ValidationError) as ctx:
            candidate.accept(actor=self.user)
        self.assertIn("知识资产", str(ctx.exception))

        asset = self.make_asset()
        candidate.accept(actor=self.user, asset=asset, reason="评审通过")
        candidate.refresh_from_db()
        self.assertEqual(candidate.state, "accepted")
        self.assertTrue(candidate.is_promotable)
        self.assertEqual(candidate.promoted_asset_id, asset.pk)

    def test_distillation_cannot_propose_l1_without_evidence(self):
        candidate = KnowledgeCandidate(
            project=self.project, kind="experience", origin="distillation",
            level="L1", dedup_key="c2",
        )
        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_state_machine_rejects_accept_from_pending(self):
        candidate = KnowledgeCandidate.objects.create(
            project=self.project, kind="rule", dedup_key="c3", state="pending"
        )
        with self.assertRaises(ValidationError):
            candidate.accept(actor=self.user, asset=self.make_asset())

    def test_confidence_must_be_within_unit_interval(self):
        candidate = KnowledgeCandidate(
            project=self.project, kind="rule", dedup_key="c4", confidence=1.5
        )
        with self.assertRaises(ValidationError):
            candidate.full_clean()


class KnowledgeConflictTests(FlywheelModelTestCase):
    def setUp(self):
        super().setUp()
        self.left = self.make_asset(key="left")
        self.right = self.make_asset(key="right")

    def test_asset_cannot_conflict_with_itself(self):
        conflict = KnowledgeConflict(
            project=self.project, left_asset=self.left, right_asset=self.left,
            conflict_type="contradiction",
        )
        with self.assertRaises(ValidationError):
            conflict.full_clean()

    def test_cross_project_conflict_is_rejected(self):
        foreign = self.make_asset(key="foreign", project=self.other_project)
        conflict = KnowledgeConflict(
            project=self.project, left_asset=self.left, right_asset=foreign,
            conflict_type="contradiction",
        )
        with self.assertRaises(ValidationError):
            conflict.full_clean()

    def test_resolution_must_be_concrete_and_is_audited(self):
        conflict = KnowledgeConflict.objects.create(
            project=self.project, left_asset=self.left, right_asset=self.right,
            conflict_type="temporal",
        )
        with self.assertRaises(ValidationError):
            conflict.resolve(actor=self.user, resolution="pending")

        conflict.resolve(actor=self.user, resolution="prefer_right", note="右条覆盖新版本")
        conflict.refresh_from_db()
        self.assertEqual(conflict.state, "resolved")
        self.assertEqual(conflict.resolution, "prefer_right")
        self.assertIsNotNone(conflict.resolved_at)
        self.assertTrue(
            KnowledgeAuditLog.objects.filter(
                project=self.project, action="conflict_resolved", entity_id=str(conflict.pk)
            ).exists()
        )

    def test_trust_penalty_is_bounded(self):
        conflict = KnowledgeConflict(
            project=self.project, left_asset=self.left, right_asset=self.right,
            conflict_type="duplicate", trust_penalty=1.4,
        )
        with self.assertRaises(ValidationError):
            conflict.full_clean()


class IndexProjectionTests(FlywheelModelTestCase):
    def test_idempotency_key_is_derived_and_unique_per_generation(self):
        version = self.make_version()
        first = IndexProjection.objects.create(
            project=self.project, version=version,
            index_type="qdrant_dense", projection_version="qwen3-emb-v1",
        )
        self.assertEqual(
            first.idempotency_key,
            IndexProjection.build_idempotency_key(version.pk, "qdrant_dense", "qwen3-emb-v1"),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            IndexProjection.objects.create(
                project=self.project, version=version,
                index_type="qdrant_dense", projection_version="qwen3-emb-v1",
            )

    def test_new_embedding_generation_coexists_for_alias_switch(self):
        version = self.make_version()
        old = IndexProjection.objects.create(
            project=self.project, version=version,
            index_type="qdrant_dense", projection_version="qwen3-emb-v1",
        )
        new = IndexProjection.objects.create(
            project=self.project, version=version,
            index_type="qdrant_dense", projection_version="sense-nova-large-v0.2.5",
        )
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(IndexProjection.objects.filter(version=version).count(), 2)

    def test_cannot_mark_ready_without_writing_stage(self):
        projection = IndexProjection.objects.create(
            project=self.project, version=self.make_version(),
            index_type="qdrant_dense", projection_version="gen-1",
        )
        with self.assertRaises(ValidationError):
            projection.mark_ready()
        projection.transition_to("writing")
        projection.mark_ready(checksum="sum-1", collection_name="kb_1_vgen-1")
        projection.refresh_from_db()
        self.assertEqual(projection.state, "ready")
        self.assertEqual(projection.checksum, "sum-1")
        self.assertIsNotNone(projection.completed_at)

    def test_failed_projection_records_error_and_attempts(self):
        projection = IndexProjection.objects.create(
            project=self.project, version=self.make_version(),
            index_type="qdrant_sparse", projection_version="gen-1",
        )
        projection.transition_to("writing")
        projection.mark_failed("qdrant 连接超时")
        projection.refresh_from_db()
        self.assertEqual(projection.state, "failed")
        self.assertEqual(projection.attempts, 1)
        self.assertIn("超时", projection.last_error)

    def test_projection_version_is_mandatory(self):
        projection = IndexProjection(
            project=self.project, version=self.make_version(),
            index_type="qdrant_dense", projection_version="",
        )
        with self.assertRaises(ValidationError):
            projection.full_clean()

    def test_cross_project_projection_is_rejected(self):
        foreign_version = self.make_version(
            asset=self.make_asset(key="fp", project=self.other_project),
            snapshot=self.make_snapshot(project=self.other_project, source_id="fs"),
        )
        projection = IndexProjection(
            project=self.project, version=foreign_version,
            index_type="qdrant_dense", projection_version="gen-1",
        )
        with self.assertRaises(ValidationError):
            projection.full_clean()


class AuditLogTests(FlywheelModelTestCase):
    def test_transition_records_actor_reason_and_states(self):
        snapshot = self.make_snapshot()
        snapshot.transition_to("parsed", actor=self.user, reason="解析完成")

        log = KnowledgeAuditLog.objects.filter(
            entity_type="SourceSnapshot", entity_id=str(snapshot.pk)
        ).latest("created_at")
        self.assertEqual(log.action, "transition")
        self.assertEqual(log.from_state, "captured")
        self.assertEqual(log.to_state, "parsed")
        self.assertEqual(log.reason, "解析完成")
        self.assertEqual(log.actor_id, self.user.pk)
        self.assertEqual(log.actor_type, "user")
        self.assertEqual(log.project_id, self.project.pk)

    def test_system_transition_is_marked_as_system_actor(self):
        candidate = KnowledgeCandidate.objects.create(
            project=self.project, kind="rule", dedup_key="audit-1"
        )
        candidate.transition_to("evaluating", reason="自动排入评测")
        log = KnowledgeAuditLog.objects.filter(entity_id=str(candidate.pk)).latest("created_at")
        self.assertIsNone(log.actor_id)
        self.assertEqual(log.actor_type, "system")

    def test_audit_log_is_append_only_shape(self):
        """审计表只追加：字段齐备且不带 updated_at（不可就地修改）。"""
        field_names = {f.name for f in KnowledgeAuditLog._meta.get_fields()}
        self.assertNotIn("updated_at", field_names)
        self.assertTrue({"actor", "action", "reason", "created_at"} <= field_names)
