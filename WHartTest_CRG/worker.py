"""Isolated CRG query worker.

The API launches this module in a subprocess with a task-scoped
``CRG_DATA_DIR``.  Keeping imports in this process avoids sharing CRG's global
configuration between requests.
"""
import json
import sys
from pathlib import Path


def _relative_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid changed file path: {value}")
    return path.as_posix()


def main() -> int:
    request = json.load(sys.stdin)
    repo_root = str(Path(request["repo_root"]).resolve())
    changed_files = list(dict.fromkeys(
        _relative_path(str(item)) for item in request.get("changed_files", [])
    ))
    base = str(request["base_sha"])
    limits = request.get("limits") or {}

    from code_review_graph.tools.review import (
        detect_changes_func,
        get_affected_flows_func,
        get_review_context,
    )

    detect = detect_changes_func(
        base=base,
        changed_files=changed_files,
        include_source=False,
        max_depth=int(request.get("max_depth", 2)),
        repo_root=repo_root,
        detail_level="standard",
        max_results=min(int(limits.get("callers", 100)), 200),
        max_flows=min(int(limits.get("affected_flows", 25)), 200),
    )
    review = get_review_context(
        changed_files=changed_files,
        max_depth=int(request.get("max_depth", 2)),
        include_source=True,
        max_lines_per_file=200,
        repo_root=repo_root,
        base=base,
        detail_level="standard",
        max_results=min(int(limits.get("callers", 100)), 200),
        max_files=min(int(limits.get("affected_files", 200)), 200),
    )
    flows = get_affected_flows_func(
        changed_files=changed_files,
        base=base,
        repo_root=repo_root,
        detail_level="minimal",
        max_flows=min(int(limits.get("affected_flows", 25)), 200),
    )
    print(json.dumps({"detect": detect, "review": review, "flows": flows}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
