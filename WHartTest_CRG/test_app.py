from pathlib import Path
import tempfile
import unittest
import uuid
import json
import sqlite3
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import (
    CancelRequest,
    BrowseRequest,
    ContextRequest,
    _authorize,
    _normalize_record,
    _relative_file,
    _safe_child,
    cancel,
    browse,
    delete_graph,
)


class AppHelpersTest(unittest.TestCase):
    def test_relative_file_rejects_repository_escape(self):
        with self.assertRaises(HTTPException):
            _relative_file("../secret.py")

    def test_safe_child_rejects_nested_path(self):
        with self.assertRaises(HTTPException):
            _safe_child(Path("/repositories"), "task/../../outside")

    def test_normalize_record_scrubs_nested_repository_paths(self):
        repo = Path("/repositories/task-id")
        record = {
            "file": "/repositories/task-id/src/app.py",
            "covered_by": ["/repositories/task-id/tests/test_app.py::test_ok"],
            "nested": {"target": "/repositories/task-id/src/service.py::run"},
        }
        self.assertEqual(_normalize_record(record, repo), {
            "file": "src/app.py",
            "covered_by": ["tests/test_app.py::test_ok"],
            "nested": {"target": "src/service.py::run"},
        })

    def test_authorization_rejects_missing_or_wrong_token(self):
        with patch("app.INTERNAL_TOKEN", "expected"):
            for header in (None, "Bearer wrong", "Basic expected"):
                with self.subTest(header=header), self.assertRaises(HTTPException) as raised:
                    _authorize(header)
                self.assertEqual(raised.exception.status_code, 401)
            self.assertIsNone(_authorize("Bearer expected"))

    def test_context_contract_rejects_unsafe_limits_and_oversized_file_list(self):
        identity = {
            "task_id": str(uuid.uuid4()),
            "repository_id": 1,
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
        }
        with self.assertRaises(ValidationError):
            ContextRequest(**identity, changed_files=["a.py"], limits={"source_lines": 801})
        with self.assertRaises(ValidationError):
            ContextRequest(**identity, changed_files=["a.py"] * 2001)

    def test_cancel_terminates_active_process_group(self):
        task_id = uuid.uuid4()
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch.dict("app._processes", {str(task_id): process}, clear=True), patch("app.os.killpg") as killpg:
            response = cancel(CancelRequest(task_id=task_id))
        self.assertEqual(response["status"], "cancelled")
        killpg.assert_called_once()

    def test_delete_is_scoped_to_requested_graph_directory(self):
        task_id = uuid.uuid4()
        with tempfile.TemporaryDirectory() as root:
            graph = Path(root) / str(task_id)
            graph.mkdir()
            (graph / "graph.db").write_text("test", encoding="utf-8")
            sibling = Path(root) / "keep"
            sibling.mkdir()
            with patch("app.GRAPH_ROOT", Path(root)), patch.dict("app._processes", {}, clear=True):
                response = delete_graph(CancelRequest(task_id=task_id))
            self.assertEqual(response["status"], "deleted")
            self.assertFalse(graph.exists())
            self.assertTrue(sibling.exists())

    def test_browse_returns_bounded_relative_graph_with_provenance(self):
        task_id = uuid.uuid4()
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root) / "repo"
            graph = Path(root) / "graph"
            repo.mkdir(); graph.mkdir()
            database = graph / "graph.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE nodes (
                    id INTEGER PRIMARY KEY, kind TEXT, name TEXT, qualified_name TEXT,
                    file_path TEXT, line_start INTEGER, line_end INTEGER, language TEXT,
                    parent_name TEXT, return_type TEXT, is_test INTEGER, signature TEXT,
                    community_id INTEGER
                );
                CREATE TABLE edges (
                    id INTEGER PRIMARY KEY, kind TEXT, source_qualified TEXT,
                    target_qualified TEXT, file_path TEXT, line INTEGER,
                    confidence REAL, confidence_tier TEXT, target_resolution TEXT
                );
                CREATE TABLE risk_index (
                    node_id INTEGER, risk_score REAL, caller_count INTEGER, test_coverage TEXT
                );
            """)
            source = f"{repo}/src/service.py::run"
            target = f"{repo}/tests/test_service.py::test_run"
            connection.executemany(
                "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, "Function", "run", source, f"{repo}/src/service.py", 10, 20, "python", "", "", 0, "run()", None),
                    (2, "Test", "test_run", target, f"{repo}/tests/test_service.py", 3, 8, "python", "", "", 1, "test_run()", None),
                ],
            )
            connection.execute("INSERT INTO edges VALUES (1,'CALLS',?,?,?,?,1.0,'EXTRACTED','direct')", (target, source, f"{repo}/tests/test_service.py", 5))
            connection.execute("INSERT INTO risk_index VALUES (1,0.8,3,'covered')")
            connection.commit(); connection.close()
            (graph / "metadata.json").write_text(json.dumps({"repository_id": 7, "head_sha": "b" * 40}), encoding="utf-8")
            request = BrowseRequest(
                task_id=task_id, repository_id=7, base_sha="a" * 40,
                head_sha="b" * 40, center="1", limit=10,
            )
            with patch("app._paths_and_identity", return_value=(repo, graph)):
                result = browse(request)
            self.assertEqual({node["id"] for node in result["nodes"]}, {"1", "2"})
            self.assertEqual(result["edges"][0]["kind"], "CALLS")
            self.assertEqual(result["nodes"][0]["provenance"]["source_type"], "code_repository")
            self.assertFalse(any(str(repo) in json.dumps(node) for node in result["nodes"]))


if __name__ == "__main__":
    unittest.main()
