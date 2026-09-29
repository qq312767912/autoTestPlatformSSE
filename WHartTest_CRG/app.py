import json
import os
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


CRG_VERSION = "2.3.9"
REPOSITORY_ROOT = Path(os.environ.get("CRG_REPOSITORY_ROOT", "/repositories")).resolve()
GRAPH_ROOT = Path(os.environ.get("CRG_GRAPH_ROOT", "/graphs")).resolve()
INTERNAL_TOKEN = os.environ.get("CRG_INTERNAL_TOKEN", "")
BUILD_TIMEOUT = int(os.environ.get("CRG_BUILD_TIMEOUT", "600"))
QUERY_TIMEOUT = int(os.environ.get("CRG_QUERY_TIMEOUT", "120"))
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

app = FastAPI(title="WHartTest Code Review Graph", docs_url=None, redoc_url=None)
_processes: dict[str, subprocess.Popen] = {}
_process_lock = threading.Lock()


class GraphIdentity(BaseModel):
    task_id: uuid.UUID
    repository_id: int = Field(gt=0)
    base_sha: str = Field(min_length=40, max_length=64)
    head_sha: str = Field(min_length=40, max_length=64)


class PrepareRequest(GraphIdentity):
    force_rebuild: bool = False


class QueryLimits(BaseModel):
    affected_files: int = Field(default=200, ge=1, le=200)
    callers: int = Field(default=100, ge=1, le=200)
    related_tests: int = Field(default=100, ge=1, le=200)
    affected_flows: int = Field(default=25, ge=1, le=200)
    source_lines: int = Field(default=800, ge=1, le=800)


class ContextRequest(GraphIdentity):
    changed_files: list[str] = Field(min_length=1, max_length=2000)
    max_depth: int = Field(default=2, ge=1, le=5)
    limits: QueryLimits = Field(default_factory=QueryLimits)


class CancelRequest(BaseModel):
    task_id: uuid.UUID


class BrowseRequest(GraphIdentity):
    search: str = Field(default="", max_length=200)
    node_kinds: list[str] = Field(default_factory=list, max_length=20)
    edge_kinds: list[str] = Field(default_factory=list, max_length=20)
    center: str = Field(default="", max_length=2000)
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=120, ge=10, le=300)


def _authorize(authorization: str | None = Header(default=None)) -> None:
    if not INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail={"code": "service_not_configured"})
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not secrets.compare_digest(supplied, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})


def _safe_child(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if target.parent != root:
        raise HTTPException(status_code=400, detail={"code": "invalid_request"})
    return target


def _relative_file(raw: str) -> str:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise HTTPException(status_code=400, detail={"code": "invalid_changed_file"})
    return path.as_posix()


def _relative_graph_path(value: Any, repo: Path) -> Any:
    """Remove container-private repository prefixes from CRG output."""
    if not isinstance(value, str):
        return value
    prefix = f"{repo.as_posix()}/"
    return value[len(prefix):] if value.startswith(prefix) else value


def _normalize_value(value: Any, repo: Path) -> Any:
    if isinstance(value, str):
        return _relative_graph_path(value, repo)
    if isinstance(value, list):
        return [_normalize_value(item, repo) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item, repo) for key, item in value.items()}
    return value


def _normalize_record(record: dict[str, Any], repo: Path) -> dict[str, Any]:
    return _normalize_value(record, repo)


def _safe_graph_filters(values: list[str], *, node: bool) -> list[str]:
    pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$" if node else r"^[A-Z][A-Z0-9_]{0,63}$")
    normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if any(not pattern.fullmatch(value) for value in normalized):
        raise HTTPException(status_code=400, detail={"code": "invalid_graph_filter"})
    return normalized


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def _graph_node(row: sqlite3.Row, repo: Path, request: BrowseRequest) -> dict[str, Any]:
    path = _relative_graph_path(row["file_path"] or "", repo)
    qualified = _relative_graph_path(row["qualified_name"] or "", repo)
    raw_name = _relative_graph_path(row["name"] or qualified, repo)
    label = Path(raw_name).name if row["kind"] == "File" else raw_name
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "label": label,
        "qualified_name": qualified,
        "path": path,
        "location": {"line_start": row["line_start"], "line_end": row["line_end"]},
        "language": row["language"] or "",
        "is_test": bool(row["is_test"]),
        "properties": {
            "signature": row["signature"] or "",
            "parent_name": row["parent_name"] or "",
            "return_type": row["return_type"] or "",
            "community_id": row["community_id"],
            "risk_score": float(row["risk_score"] or 0),
            "caller_count": int(row["caller_count"] or 0),
            "test_coverage": row["test_coverage"] or "unknown",
        },
        "provenance": {
            "source_type": "code_repository",
            "source_id": str(request.repository_id),
            "snapshot_id": str(request.task_id),
            "confidence": 1.0,
            "location": path,
        },
    }


def _graph_facets(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "node_kinds": {row[0]: row[1] for row in connection.execute(
            "SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY COUNT(*) DESC"
        )},
        "edge_kinds": {row[0]: row[1] for row in connection.execute(
            "SELECT kind, COUNT(*) FROM edges GROUP BY kind ORDER BY COUNT(*) DESC"
        )},
        "languages": {row[0] or "unknown": row[1] for row in connection.execute(
            "SELECT language, COUNT(*) FROM nodes GROUP BY language ORDER BY COUNT(*) DESC"
        )},
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise HTTPException(status_code=422, detail={"code": "identity_mismatch"})
    return result.stdout.strip()


def _paths_and_identity(request: GraphIdentity) -> tuple[Path, Path]:
    if not SHA_RE.fullmatch(request.base_sha) or not SHA_RE.fullmatch(request.head_sha):
        raise HTTPException(status_code=400, detail={"code": "invalid_sha"})
    task_name = str(request.task_id)
    repo = _safe_child(REPOSITORY_ROOT, task_name)
    graph = _safe_child(GRAPH_ROOT, task_name)
    if not repo.is_dir() or not (repo / ".git").exists():
        raise HTTPException(status_code=404, detail={"code": "repository_not_ready"})
    try:
        marker = (repo / ".repository-id").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(status_code=422, detail={"code": "identity_mismatch"}) from exc
    if marker != str(request.repository_id) or _git(repo, "rev-parse", "HEAD") != request.head_sha:
        raise HTTPException(status_code=422, detail={"code": "identity_mismatch"})
    return repo, graph


def _run(task_id: str, command: list[str], *, env: dict[str, str], payload: dict[str, Any] | None, timeout: int) -> tuple[str, str]:
    with _process_lock:
        if task_id in _processes:
            raise HTTPException(status_code=409, detail={"code": "graph_busy"})
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        _processes[task_id] = process
    try:
        stdout, stderr = process.communicate(
            json.dumps(payload) if payload is not None else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise HTTPException(status_code=504, detail={"code": "timeout"}) from exc
    finally:
        with _process_lock:
            _processes.pop(task_id, None)
    if process.returncode:
        raise HTTPException(
            status_code=500,
            detail={"code": "crg_failed", "message": stderr[-1000:]},
        )
    return stdout, stderr


def _status(repo: Path, graph: Path, env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        ["code-review-graph", "status", "--repo", str(repo), "--data-dir", str(graph), "--json"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail={"code": "status_failed"})
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail={"code": "invalid_crg_output"}) from exc


@app.get("/health")
def health():
    return {
        "status": "ok",
        "crg_version": CRG_VERSION,
        "writable_graph_root": os.access(GRAPH_ROOT, os.W_OK),
    }


@app.post("/v1/graphs/prepare", dependencies=[Depends(_authorize)])
def prepare(request: PrepareRequest):
    started = time.monotonic()
    repo, graph = _paths_and_identity(request)
    graph.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CRG_DATA_DIR"] = str(graph)
    db = graph / "graph.db"
    operation = "build" if request.force_rebuild or not db.exists() else "update"
    if request.force_rebuild and db.exists():
        for candidate in graph.glob("graph.db*"):
            candidate.unlink()
    command = ["code-review-graph", operation, "--repo", str(repo), "--data-dir", str(graph), "--quiet"]
    if operation == "update":
        command.extend(["--base", request.base_sha])
    _run(str(request.task_id), command, env=env, payload=None, timeout=BUILD_TIMEOUT)
    status = _status(repo, graph, env)
    metadata = {
        "task_id": str(request.task_id),
        "repository_id": request.repository_id,
        "base_sha": request.base_sha,
        "head_sha": request.head_sha,
        "crg_version": CRG_VERSION,
    }
    temporary = graph / "metadata.json.tmp"
    temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    temporary.replace(graph / "metadata.json")
    errors = status.get("errors") or []
    return {
        "status": "completed",
        "operation": operation,
        "graph_commit": request.head_sha,
        "parse_status": "partial" if errors else "complete",
        "failed_files": errors[:100],
        "stats": status,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "request_id": str(uuid.uuid4()),
        "crg_version": CRG_VERSION,
    }


@app.post("/v1/graphs/context", dependencies=[Depends(_authorize)])
def context(request: ContextRequest):
    started = time.monotonic()
    repo, graph = _paths_and_identity(request)
    metadata_file = graph / "metadata.json"
    if not (graph / "graph.db").is_file() or not metadata_file.is_file():
        raise HTTPException(status_code=404, detail={"code": "graph_not_ready"})
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    if metadata.get("repository_id") != request.repository_id or metadata.get("head_sha") != request.head_sha:
        raise HTTPException(status_code=422, detail={"code": "identity_mismatch"})
    changed_files = list(dict.fromkeys(_relative_file(item) for item in request.changed_files))
    env = os.environ.copy()
    env["CRG_DATA_DIR"] = str(graph)
    payload = {
        "repo_root": str(repo),
        "base_sha": request.base_sha,
        "changed_files": changed_files,
        "max_depth": request.max_depth,
        "limits": request.limits.model_dump(),
    }
    stdout, _ = _run(
        str(request.task_id), [sys.executable, "/app/worker.py"],
        env=env, payload=payload, timeout=QUERY_TIMEOUT,
    )
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail={"code": "invalid_crg_output"}) from exc
    detect = raw.get("detect") or {}
    review = raw.get("review") or {}
    flows = raw.get("flows") or {}
    if any(item.get("status") == "error" for item in (detect, review, flows)):
        raise HTTPException(status_code=500, detail={"code": "crg_query_failed"})
    review_context = review.get("context") or review
    changed_functions = [
        _normalize_record(item, repo) for item in (detect.get("changed_functions") or [])
    ]
    affected_files = (
        review_context.get("impacted_files")
        or review.get("impacted_files")
        or detect.get("affected_files")
        or []
    )
    affected_files = [_relative_graph_path(item, repo) for item in affected_files]
    graph = review_context.get("graph") or {}
    changed_nodes = graph.get("changed_nodes") or []
    impacted_nodes = graph.get("impacted_nodes") or []
    edges = graph.get("edges") or []
    callers = [
        _normalize_record(item, repo) for item in edges
        if str(item.get("kind", "")).upper() in {"CALLS", "IMPORTS"}
    ][:request.limits.callers]
    related_tests = [
        _normalize_record(item, repo) for item in (*changed_nodes, *impacted_nodes)
        if item.get("is_test")
        or re.search(r"(^|/)(tests?|__tests__)(/|$)|(^|/)(test_|.*[._]spec\.)", str(item.get("file_path", "")), re.I)
    ]
    related_tests = list({str(item.get("qualified_name") or item.get("id")): item for item in related_tests}.values())[
        :request.limits.related_tests
    ]
    truncation = {
        "truncated": bool(detect.get("truncated") or review.get("truncated") or flows.get("truncated")),
        "totals": {
            "changed_functions": detect.get("changed_functions_total", len(changed_functions)),
            "affected_flows": flows.get("total", len(flows.get("affected_flows") or [])),
        },
        "returned": {
            "changed_functions": len(changed_functions),
            "affected_flows": len(flows.get("affected_flows") or []),
        },
    }
    return {
        "status": "completed",
        "graph_commit": request.head_sha,
        "changed_symbols": changed_functions,
        "affected_files": affected_files,
        "callers": callers,
        "related_tests": related_tests,
        "affected_flows": [
            _normalize_record(item, repo) for item in (flows.get("affected_flows") or [])
        ],
        "test_gaps": [
            _normalize_record(item, repo) for item in (detect.get("test_gaps") or [])
        ],
        "source_snippets": _normalize_value(
            review.get("source_snippets") or review_context.get("source_snippets") or {}, repo,
        ),
        "coverage": {
            "requested_changed_files": len(changed_files),
            "mapped_changed_files": detect.get("changed_file_count", len(changed_files)),
            "unmapped_changed_files": detect.get("unmapped_changed_files") or [],
            "parse_status": "complete",
        },
        "truncation": truncation,
        "context_savings": detect.get("context_savings") or review.get("context_savings") or {},
        "duration_ms": round((time.monotonic() - started) * 1000),
        "request_id": str(uuid.uuid4()),
        "crg_version": CRG_VERSION,
    }


@app.post("/v1/graphs/browse", dependencies=[Depends(_authorize)])
def browse(request: BrowseRequest):
    """Return a bounded, provenance-rich subgraph for UI and future retrieval adapters."""
    started = time.monotonic()
    repo, graph_root = _paths_and_identity(request)
    database = graph_root / "graph.db"
    metadata_file = graph_root / "metadata.json"
    if not database.is_file() or not metadata_file.is_file():
        raise HTTPException(status_code=404, detail={"code": "graph_not_ready"})
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail={"code": "invalid_graph_metadata"}) from exc
    if metadata.get("repository_id") != request.repository_id or metadata.get("head_sha") != request.head_sha:
        raise HTTPException(status_code=422, detail={"code": "identity_mismatch"})

    node_kinds = _safe_graph_filters(request.node_kinds, node=True)
    edge_kinds = _safe_graph_filters(request.edge_kinds, node=False)
    uri = f"file:{database.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        total_nodes = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        total_edges = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        facets = _graph_facets(connection)

        node_columns = """
            n.id, n.kind, n.name, n.qualified_name, n.file_path, n.line_start, n.line_end,
            n.language, n.parent_name, n.return_type, n.is_test, n.signature, n.community_id,
            COALESCE(r.risk_score, 0) AS risk_score,
            COALESCE(r.caller_count, 0) AS caller_count,
            COALESCE(r.test_coverage, 'unknown') AS test_coverage
        """
        selected: dict[str, sqlite3.Row] = {}
        edge_rows: list[sqlite3.Row] = []

        if request.center:
            center_row = connection.execute(
                f"SELECT {node_columns} FROM nodes n LEFT JOIN risk_index r ON r.node_id=n.id "
                "WHERE CAST(n.id AS TEXT)=? OR n.qualified_name=? LIMIT 1",
                (request.center, request.center),
            ).fetchone()
            if not center_row:
                raise HTTPException(status_code=404, detail={"code": "graph_node_not_found"})
            selected[center_row["qualified_name"]] = center_row
            frontier = {center_row["qualified_name"]}
            for _ in range(request.depth):
                if not frontier or len(selected) >= request.limit:
                    break
                params: list[Any] = [*frontier, *frontier]
                edge_where = (
                    f"(source_qualified IN ({_placeholders(list(frontier))}) "
                    f"OR target_qualified IN ({_placeholders(list(frontier))}))"
                )
                if edge_kinds:
                    edge_where += f" AND kind IN ({_placeholders(edge_kinds)})"
                    params.extend(edge_kinds)
                candidates = connection.execute(
                    f"SELECT * FROM edges WHERE {edge_where} ORDER BY confidence DESC, id LIMIT 800",
                    params,
                ).fetchall()
                edge_rows.extend(candidates)
                names = {
                    value for edge in candidates
                    for value in (edge["source_qualified"], edge["target_qualified"])
                    if value not in selected
                }
                if not names:
                    break
                node_params: list[Any] = list(names)
                node_where = f"n.qualified_name IN ({_placeholders(list(names))})"
                if node_kinds:
                    node_where += f" AND n.kind IN ({_placeholders(node_kinds)})"
                    node_params.extend(node_kinds)
                remaining = request.limit - len(selected)
                rows = connection.execute(
                    f"SELECT {node_columns} FROM nodes n LEFT JOIN risk_index r ON r.node_id=n.id "
                    f"WHERE {node_where} ORDER BY risk_score DESC, caller_count DESC, n.id LIMIT ?",
                    [*node_params, remaining],
                ).fetchall()
                frontier = {row["qualified_name"] for row in rows}
                selected.update({row["qualified_name"]: row for row in rows})
        else:
            params = []
            clauses = []
            search = request.search.strip()
            if search:
                pattern = f"%{search.replace('%', '').replace('_', '')}%"
                clauses.append("(n.name LIKE ? OR n.qualified_name LIKE ? OR n.file_path LIKE ?)")
                params.extend([pattern, pattern, pattern])
            if node_kinds:
                clauses.append(f"n.kind IN ({_placeholders(node_kinds)})")
                params.extend(node_kinds)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = connection.execute(
                f"SELECT {node_columns} FROM nodes n LEFT JOIN risk_index r ON r.node_id=n.id {where} "
                "ORDER BY risk_score DESC, caller_count DESC, n.is_test DESC, n.id LIMIT ?",
                [*params, request.limit],
            ).fetchall()
            selected = {row["qualified_name"]: row for row in rows}

        qualified_names = list(selected)
        if qualified_names and not edge_rows:
            edge_params: list[Any] = [*qualified_names, *qualified_names]
            edge_where = (
                f"source_qualified IN ({_placeholders(qualified_names)}) "
                f"AND target_qualified IN ({_placeholders(qualified_names)})"
            )
            if edge_kinds:
                edge_where += f" AND kind IN ({_placeholders(edge_kinds)})"
                edge_params.extend(edge_kinds)
            edge_rows = connection.execute(
                f"SELECT * FROM edges WHERE {edge_where} ORDER BY confidence DESC, id LIMIT 800",
                edge_params,
            ).fetchall()

        id_by_qualified = {qualified: str(row["id"]) for qualified, row in selected.items()}
        edges = []
        seen_edges = set()
        for row in edge_rows:
            source = id_by_qualified.get(row["source_qualified"])
            target = id_by_qualified.get(row["target_qualified"])
            if not source or not target or row["id"] in seen_edges:
                continue
            seen_edges.add(row["id"])
            edges.append({
                "id": str(row["id"]),
                "kind": row["kind"],
                "source": source,
                "target": target,
                "confidence": float(row["confidence"] or 0),
                "properties": {
                    "line": row["line"],
                    "confidence_tier": row["confidence_tier"] or "",
                    "target_resolution": row["target_resolution"] or "",
                },
                "provenance": {
                    "source_type": "code_repository",
                    "source_id": str(request.repository_id),
                    "snapshot_id": str(request.task_id),
                    "confidence": float(row["confidence"] or 0),
                    "location": _relative_graph_path(row["file_path"] or "", repo),
                },
            })
        nodes = [_graph_node(row, repo, request) for row in selected.values()]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail={"code": "graph_read_failed"}) from exc
    finally:
        if "connection" in locals():
            connection.close()

    return {
        "status": "completed",
        "source": {
            "type": "code_repository",
            "id": str(request.repository_id),
            "snapshot_id": str(request.task_id),
            "commit": request.head_sha,
        },
        "stats": {"nodes": total_nodes, "edges": total_edges},
        "facets": facets,
        "nodes": nodes,
        "edges": edges,
        "truncated": len(nodes) < total_nodes,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "request_id": str(uuid.uuid4()),
        "crg_version": CRG_VERSION,
    }


@app.post("/v1/graphs/cancel", dependencies=[Depends(_authorize)])
def cancel(request: CancelRequest):
    with _process_lock:
        process = _processes.get(str(request.task_id))
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        return {"status": "cancelled", "task_id": str(request.task_id)}
    return {"status": "idle", "task_id": str(request.task_id)}


@app.post("/v1/graphs/delete", dependencies=[Depends(_authorize)])
def delete_graph(request: CancelRequest):
    task_id = str(request.task_id)
    with _process_lock:
        process = _processes.get(task_id)
    if process and process.poll() is None:
        raise HTTPException(status_code=409, detail={"code": "graph_busy"})
    graph = _safe_child(GRAPH_ROOT, task_id)
    if graph.exists():
        shutil.rmtree(graph)
        return {"status": "deleted", "task_id": task_id}
    return {"status": "missing", "task_id": task_id}
