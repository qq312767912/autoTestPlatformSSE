"""版本化 Qdrant 投影服务（specs/knowledge-flywheel/tasks.md 任务 7）。

实现 collection + alias 双缓冲切换、Outbox 幂等写入、重建与回滚。
当前仅实现 qdrant_dense / qdrant_sparse 两类索引；document_graph / keyword
留给任务 8 及后续扩展。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    NamedSparseVector,
    NamedVector,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from .knowledge_models import IndexProjection, IndexProjectionOutbox

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
BATCH_SIZE = 100
DEFAULT_VECTOR_SIZE = 384


def _get_qdrant_url() -> str:
    return getattr(settings, "QDRANT_URL", None) or os.environ.get("QDRANT_URL", "http://localhost:8918")


def _get_vector_size() -> int:
    return int(getattr(settings, "KE_PROJECTION_VECTOR_SIZE", None) or os.environ.get("KE_PROJECTION_VECTOR_SIZE", DEFAULT_VECTOR_SIZE))


class ProjectionService:
    """管理 KnowledgeVersion 到 Qdrant collection 的投影生命周期。"""

    def __init__(self, client: Optional[QdrantClient] = None, vector_size: Optional[int] = None):
        self._client = client
        self.vector_size = vector_size or _get_vector_size()

    # ------------------------------------------------------------------ 客户端

    def get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(url=_get_qdrant_url())
        return self._client

    # ------------------------------------------------------------------ 命名

    @staticmethod
    def logical_alias(project_id, index_type: str) -> str:
        """项目级逻辑别名，检索侧只认 alias。"""
        return f"ke_{project_id}_{index_type}"

    @staticmethod
    def physical_collection_name(projection: IndexProjection) -> str:
        """物理 collection 名，含 projection id 前缀，避免并发重建冲突。"""
        name = (
            f"ke_{projection.project_id}_{projection.index_type}_"
            f"{projection.projection_version}_{str(projection.id)[:8]}"
        )
        if len(name) > 255:
            name = name[:255]
        return name

    # ------------------------------------------------------------------ 创建 / 暂存

    @staticmethod
    def create_projection(
        version,
        index_type: str = "qdrant_dense",
        projection_version: str = "v1",
    ) -> IndexProjection:
        projection = IndexProjection(
            project_id=version.project_id,
            version=version,
            index_type=index_type,
            projection_version=projection_version,
        )
        projection.idempotency_key = IndexProjection.build_idempotency_key(
            version.id, index_type, projection_version
        )
        projection.full_clean()
        projection.save()
        return projection

    @staticmethod
    def stage_upserts(
        projection: IndexProjection,
        points: Iterable[Dict[str, Any]],
    ) -> List[IndexProjectionOutbox]:
        """把待写入点放入 Outbox；同一 vector_id 多次暂存会覆盖为最新一条。"""
        records = []
        for point in points:
            vector_id = str(point["id"])
            payload = {
                "vector": point.get("vector") or [],
                "sparse_vector": point.get("sparse_vector") or None,
                "payload": point.get("payload") or {},
            }
            key = IndexProjectionOutbox.build_idempotency_key(projection.id, vector_id)
            record, _ = IndexProjectionOutbox.objects.update_or_create(
                idempotency_key=key,
                defaults={
                    "projection": projection,
                    "vector_id": vector_id,
                    "operation": "upsert",
                    "payload": payload,
                    "state": "pending",
                    "attempts": 0,
                    "last_error": "",
                },
            )
            records.append(record)
        return records

    @staticmethod
    def stage_deletes(
        projection: IndexProjection,
        vector_ids: Iterable[str],
    ) -> List[IndexProjectionOutbox]:
        records = []
        for vid in vector_ids:
            vector_id = str(vid)
            key = IndexProjectionOutbox.build_idempotency_key(projection.id, vector_id)
            record, _ = IndexProjectionOutbox.objects.update_or_create(
                idempotency_key=key,
                defaults={
                    "projection": projection,
                    "vector_id": vector_id,
                    "operation": "delete",
                    "payload": {},
                    "state": "pending",
                    "attempts": 0,
                    "last_error": "",
                },
            )
            records.append(record)
        return records

    # ------------------------------------------------------------------ 构建

    def build(self, projection_id: str) -> IndexProjection:
        """从 Outbox 消费写入，创建/切换 alias，并使投影 ready。"""
        with transaction.atomic():
            projection = (
                IndexProjection.objects.select_for_update()
                .get(pk=projection_id)
            )
            if projection.state != "writing":
                projection.transition_to("writing", reason="开始构建投影")

        collection_name = self.physical_collection_name(projection)
        alias_name = self.logical_alias(projection.project_id, projection.index_type)
        client = self.get_client()

        try:
            self._ensure_collection(client, collection_name, projection.index_type)
            pending = self._latest_pending_outbox(projection)
            self._flush_outbox(client, collection_name, pending)
            self._switch_alias(client, collection_name, alias_name)

            checksum = self._checksum_points(pending)
            projection.mark_ready(checksum=checksum, collection_name=collection_name)
            self._demote_siblings(projection)
            logger.info(f"投影 {projection_id} 已就绪，alias={alias_name} -> {collection_name}")
            return projection
        except Exception as exc:  # noqa: BLE001
            logger.exception("投影构建失败: %s", projection_id)
            projection.mark_failed(str(exc))
            raise

    def rebuild(self, projection_id: str) -> IndexProjection:
        """删除物理 collection 后重新构建（用于修复损坏投影）。"""
        projection = IndexProjection.objects.get(pk=projection_id)
        if projection.collection_name:
            try:
                self.get_client().delete_collection(projection.collection_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"重建时删除旧 collection 失败: {exc}")
        projection.transition_to("writing", reason="重建投影")
        return self.build(projection_id)

    # ------------------------------------------------------------------ 回滚

    def rollback(self, project_id, index_type: str = "qdrant_dense") -> Optional[IndexProjection]:
        """将 alias 切回上一版 ready/stale/rolled_back 投影。"""
        candidates = list(
            IndexProjection.objects.filter(
                project_id=project_id,
                index_type=index_type,
                state__in=["ready", "stale", "rolled_back"],
            )
            .exclude(collection_name="")
            .order_by("-requested_at")
        )
        if len(candidates) < 2:
            raise RuntimeError("可回滚的上一版本不存在")

        current = candidates[0]
        previous = candidates[1]

        alias_name = self.logical_alias(project_id, index_type)
        client = self.get_client()
        self._switch_alias(client, previous.collection_name, alias_name)

        current.mark_rolled_back()
        if previous.state != "ready":
            previous.transition_to("ready", reason="回滚恢复")
        logger.info(f"投影已回滚: {alias_name} -> {previous.collection_name}")
        return previous

    # ------------------------------------------------------------------ 内部

    def _ensure_collection(
        self,
        client: QdrantClient,
        collection_name: str,
        index_type: str,
    ) -> None:
        if client.collection_exists(collection_name):
            return

        vectors_config: Dict[str, VectorParams] = {
            DENSE_VECTOR_NAME: VectorParams(
                size=self.vector_size, distance=Distance.COSINE
            )
        }
        sparse_vectors_config: Optional[Dict[str, SparseVectorParams]] = None
        if index_type == "qdrant_sparse":
            sparse_vectors_config = {
                SPARSE_VECTOR_NAME: SparseVectorParams(
                    index=SparseIndexParams(on_disk=False)
                )
            }

        client.create_collection(
            collection_name=collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
        )
        logger.info(f"创建投影 collection: {collection_name}")

    def _latest_pending_outbox(
        self, projection: IndexProjection
    ) -> List[IndexProjectionOutbox]:
        """按 vector_id 去重，只返回最新一条 pending 记录。"""
        pending = list(
            IndexProjectionOutbox.objects.filter(
                projection=projection, state="pending"
            ).order_by("-created_at")
        )
        seen: Dict[str, IndexProjectionOutbox] = {}
        for record in pending:
            if record.vector_id not in seen:
                seen[record.vector_id] = record
        return list(seen.values())

    def _flush_outbox(
        self,
        client: QdrantClient,
        collection_name: str,
        records: List[IndexProjectionOutbox],
    ) -> None:
        upserts: List[PointStruct] = []
        deletes: List[str] = []

        for record in records:
            record.state = "processing"
        if records:
            IndexProjectionOutbox.objects.bulk_update(records, ["state"])

        for record in records:
            if record.operation == "delete":
                deletes.append(record.vector_id)
                continue
            payload = record.payload or {}
            vector = payload.get("vector") or []
            sparse = payload.get("sparse_vector")
            point_payload = payload.get("payload") or {}
            # 写入投影版本、ACL、来源版本等元信息
            point_payload["_projection_id"] = str(record.projection_id)
            point_payload["_projection_version"] = record.projection.projection_version
            point_payload["_version_id"] = str(record.projection.version_id)

            point_kwargs: Dict[str, Any] = {
                "id": record.vector_id,
                "vector": {DENSE_VECTOR_NAME: vector},
                "payload": point_payload,
            }
            if sparse:
                point_kwargs["vector"][SPARSE_VECTOR_NAME] = SparseVector(
                    indices=sparse.get("indices", []),
                    values=sparse.get("values", []),
                )
            upserts.append(PointStruct(**point_kwargs))

        for i in range(0, len(upserts), BATCH_SIZE):
            client.upsert(
                collection_name=collection_name,
                points=upserts[i : i + BATCH_SIZE],
            )
        if deletes:
            client.delete(
                collection_name=collection_name,
                points_selector=deletes,
            )

        for record in records:
            record.mark_done()

    def _switch_alias(
        self,
        client: QdrantClient,
        collection_name: str,
        alias_name: str,
    ) -> None:
        """原子切换 alias：先删除同名 alias，再指向新 collection。"""
        try:
            alias_response = client.get_aliases(collection_name=collection_name)
            aliases = getattr(alias_response, "aliases", [])
        except Exception:  # noqa: BLE001
            aliases = []
        existing_alias_names = {getattr(a, "alias_name", a) for a in aliases}

        if alias_name in existing_alias_names:
            client.delete_alias(alias_name=alias_name)
        client.create_alias(collection_name=collection_name, alias_name=alias_name)

    @staticmethod
    def _checksum_points(records: List[IndexProjectionOutbox]) -> str:
        """对生效记录做简单校验和，用于对账。"""
        data = []
        for r in sorted(records, key=lambda x: x.vector_id):
            data.append(f"{r.vector_id}:{r.operation}:{json.dumps(r.payload, sort_keys=True, default=str)}")
        return hashlib.sha256("\n".join(data).encode()).hexdigest()[:32]

    @staticmethod
    def _demote_siblings(projection: IndexProjection) -> None:
        """把同项目同索引类型的旧 ready 投影降级为 stale。"""
        siblings = IndexProjection.objects.filter(
            project_id=projection.project_id,
            index_type=projection.index_type,
            state="ready",
        ).exclude(pk=projection.pk)
        for sibling in siblings:
            sibling.transition_to("stale", reason="新版本投影已就绪")


# 延迟导入 os，避免模块顶层的副作用
import os  # noqa: E402
