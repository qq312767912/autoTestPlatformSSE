"""版本化 Qdrant 投影服务测试（任务 7）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from projects.models import Project

from .knowledge_models import (
    IndexProjection,
    IndexProjectionOutbox,
    KnowledgeAsset,
    KnowledgeVersion,
    SourceSnapshot,
)
from .projection import ProjectionService


class ProjectionServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ke-proj", password="pass")
        self.project = Project.objects.create(name="KE Project", creator=self.user)
        self.snapshot = SourceSnapshot.objects.create(
            project=self.project,
            source_type="document",
            source_id="doc-1",
            content_hash="a" * 64,
            parsed_text="test",
        )
        self.asset = KnowledgeAsset.objects.create(
            project=self.project,
            asset_type="rule",
            key="rule.login",
            title="登录规则",
        )
        self.version = KnowledgeVersion.objects.create(
            asset=self.asset,
            version=1,
            content="登录必须校验验证码",
            content_hash="b" * 64,
            source_snapshot=self.snapshot,
        )

    def _mock_client(self):
        client = MagicMock()
        client.collection_exists.return_value = False
        client.get_aliases.return_value = SimpleNamespace(aliases=[])
        return client

    def test_create_projection_and_stage(self):
        projection = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        self.assertEqual(projection.state, "pending")

        points = [
            {"id": "c1", "vector": [0.1] * 384, "payload": {"text": "登录"}},
            {"id": "c2", "vector": [0.2] * 384, "payload": {"text": "登出"}},
        ]
        ProjectionService.stage_upserts(projection, points)
        self.assertEqual(projection.outbox.filter(state="pending").count(), 2)

    def test_build_creates_collection_and_alias(self):
        projection = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_upserts(
            projection,
            [{"id": "c1", "vector": [0.1] * 384, "payload": {"text": "登录"}}],
        )

        client = self._mock_client()
        service = ProjectionService(client=client, vector_size=384)
        result = service.build(projection.id)

        result.refresh_from_db()
        self.assertEqual(result.state, "ready")
        self.assertTrue(result.collection_name.startswith(f"ke_{self.project.id}_qdrant_dense_v1_"))
        self.assertTrue(client.create_collection.called)
        self.assertTrue(client.upsert.called)
        self.assertTrue(client.create_alias.called)

        alias_call = client.create_alias.call_args
        self.assertEqual(alias_call.kwargs["alias_name"], f"ke_{self.project.id}_qdrant_dense")
        self.assertEqual(alias_call.kwargs["collection_name"], result.collection_name)

    def test_build_demotes_old_ready_projection(self):
        p1 = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_upserts(p1, [{"id": "c1", "vector": [0.1] * 384}])
        client = self._mock_client()
        service = ProjectionService(client=client, vector_size=384)
        service.build(p1.id)
        p1.refresh_from_db()
        self.assertEqual(p1.state, "ready")

        # 创建新版本投影
        v2 = KnowledgeVersion.objects.create(
            asset=self.asset,
            version=2,
            content="登录必须校验验证码和 MFA",
            content_hash="c" * 64,
            source_snapshot=self.snapshot,
        )
        p2 = ProjectionService.create_projection(
            v2, index_type="qdrant_dense", projection_version="v2"
        )
        ProjectionService.stage_upserts(p2, [{"id": "c1", "vector": [0.1] * 384}])
        service.build(p2.id)

        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.state, "stale")
        self.assertEqual(p2.state, "ready")

    def test_build_idempotent_outbox(self):
        projection = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_upserts(
            projection,
            [{"id": "c1", "vector": [0.1] * 384, "payload": {"v": 1}}],
        )
        ProjectionService.stage_upserts(
            projection,
            [{"id": "c1", "vector": [0.2] * 384, "payload": {"v": 2}}],
        )
        # 同一 vector_id 在 Outbox 里只保留最新一条
        self.assertEqual(projection.outbox.count(), 1)

        client = self._mock_client()
        service = ProjectionService(client=client, vector_size=384)
        service.build(projection.id)

        self.assertEqual(client.upsert.call_count, 1)
        points = client.upsert.call_args.kwargs["points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].payload["v"], 2)

    def test_rollback_switches_alias(self):
        p1 = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_upserts(p1, [{"id": "c1", "vector": [0.1] * 384}])
        client = self._mock_client()
        service = ProjectionService(client=client, vector_size=384)
        service.build(p1.id)
        p1.refresh_from_db()

        v2 = KnowledgeVersion.objects.create(
            asset=self.asset,
            version=2,
            content="登录必须校验验证码和 MFA",
            content_hash="d" * 64,
            source_snapshot=self.snapshot,
        )
        p2 = ProjectionService.create_projection(
            v2, index_type="qdrant_dense", projection_version="v2"
        )
        ProjectionService.stage_upserts(p2, [{"id": "c1", "vector": [0.1] * 384}])
        service.build(p2.id)
        p2.refresh_from_db()

        client.reset_mock()
        result = service.rollback(self.project.id, "qdrant_dense")

        self.assertEqual(result.id, p1.id)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.state, "ready")
        self.assertEqual(p2.state, "rolled_back")
        client.create_alias.assert_called_once_with(
            collection_name=p1.collection_name,
            alias_name=f"ke_{self.project.id}_qdrant_dense",
        )

    def test_rebuild_removes_old_collection(self):
        projection = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_upserts(projection, [{"id": "c1", "vector": [0.1] * 384}])
        client = self._mock_client()
        service = ProjectionService(client=client, vector_size=384)
        service.build(projection.id)
        projection.refresh_from_db()
        old_collection = projection.collection_name

        client.reset_mock()
        client.collection_exists.return_value = False
        service.rebuild(projection.id)

        client.delete_collection.assert_called_once_with(old_collection)
        projection.refresh_from_db()
        self.assertEqual(projection.state, "ready")

    def test_stage_delete_creates_delete_outbox(self):
        projection = ProjectionService.create_projection(
            self.version, index_type="qdrant_dense", projection_version="v1"
        )
        ProjectionService.stage_deletes(projection, ["c1", "c2"])
        self.assertEqual(projection.outbox.filter(operation="delete").count(), 2)
