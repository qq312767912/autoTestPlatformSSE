"""Restricted client for the task-scoped Code Review Graph service."""
import os
from dataclasses import dataclass

import requests


class GraphUnavailable(RuntimeError):
    pass


class GraphTimeout(GraphUnavailable):
    pass


class GraphProtocolError(GraphUnavailable):
    pass


@dataclass(frozen=True)
class GraphClientConfig:
    url: str
    token: str
    prepare_timeout: int
    query_timeout: int


class CodeReviewGraphClient:
    def __init__(self, config=None):
        self.config = config or GraphClientConfig(
            url=os.environ.get("CODE_REVIEW_GRAPH_URL", "http://crg-service:8080").rstrip("/"),
            token=os.environ.get("CODE_REVIEW_GRAPH_TOKEN", ""),
            prepare_timeout=int(os.environ.get("CODE_REVIEW_GRAPH_PREPARE_TIMEOUT", "610")),
            query_timeout=int(os.environ.get("CODE_REVIEW_GRAPH_QUERY_TIMEOUT", "130")),
        )

    @staticmethod
    def enabled():
        return os.environ.get("CODE_REVIEW_GRAPH_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }

    def _post(self, path, payload, timeout):
        if not self.config.token:
            raise GraphUnavailable("CRG 内部认证 Token 未配置")
        try:
            response = requests.post(
                f"{self.config.url}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.config.token}"},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise GraphTimeout("CRG 请求超时") from exc
        except requests.RequestException as exc:
            raise GraphUnavailable(f"CRG 服务不可用：{exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[-500:]
            raise GraphUnavailable(f"CRG 返回 {response.status_code}：{detail}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphProtocolError("CRG 未返回合法 JSON") from exc
        if not isinstance(payload, dict):
            raise GraphProtocolError("CRG 响应结构无效")
        return payload

    @staticmethod
    def _identity(task):
        return {
            "task_id": str(task.pk),
            "repository_id": task.repository_id,
            "base_sha": task.base_sha,
            "head_sha": task.head_sha,
        }

    def prepare(self, task, force_rebuild=False):
        return self._post(
            "/v1/graphs/prepare",
            {**self._identity(task), "force_rebuild": bool(force_rebuild)},
            self.config.prepare_timeout,
        )

    def collect_context(self, task, changed_files):
        paths = list(dict.fromkeys(str(path) for path in changed_files if path))
        return self._post(
            "/v1/graphs/context",
            {
                **self._identity(task),
                "changed_files": paths,
                "max_depth": 2,
                "limits": {
                    "affected_files": 200,
                    "callers": 100,
                    "related_tests": 100,
                    "affected_flows": 25,
                    "source_lines": 800,
                },
            },
            self.config.query_timeout,
        )

    def browse(self, task, *, search="", node_kinds=None, edge_kinds=None, center="", depth=1, limit=120):
        return self._post(
            "/v1/graphs/browse",
            {
                **self._identity(task),
                "search": str(search or "")[:200],
                "node_kinds": list(node_kinds or []),
                "edge_kinds": list(edge_kinds or []),
                "center": str(center or "")[:2000],
                "depth": max(1, min(int(depth or 1), 3)),
                "limit": max(10, min(int(limit or 120), 300)),
            },
            self.config.query_timeout,
        )

    def cancel(self, task_id):
        try:
            return self._post(
                "/v1/graphs/cancel", {"task_id": str(task_id)}, timeout=15,
            )
        except GraphUnavailable:
            return {"status": "unavailable"}

    def delete(self, task_id):
        try:
            return self._post(
                "/v1/graphs/delete", {"task_id": str(task_id)}, timeout=30,
            )
        except GraphUnavailable:
            return {"status": "unavailable"}
