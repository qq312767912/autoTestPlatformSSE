"""公开仓库快照：按允许列表与精确 Commit 获取只读代码快照。

安全边界（对应 R3/R6）：
- 仓库标识只接受 `owner/name`，域名固定 `github.com`，不接受客户端传入的任意 URL 或本地路径；
- Commit 必须是 40 位十六进制，且每对 base/head 必须在已发布样本中出现过（防止任意 clone）；
- 下载大小、文件数、耗时都有上限；路径固定落在专用目录内，越界即拒绝；
- 快照只读，运行期通过租约引用计数，被引用的快照不会被清理。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .benchmark_models import BenchmarkCase, BenchmarkRepositorySnapshot

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GITHUB_BASE = "https://github.com"

MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOT_FILES = 60000
# 大仓库（FreeCAD / kestra / taipy 级）单次 fetch 常超过 5 分钟，
# 默认给 15 分钟，并允许通过 settings 覆盖；超时后重试一次。
DEFAULT_GIT_TIMEOUT = 900
GIT_TIMEOUT = DEFAULT_GIT_TIMEOUT
GIT_FETCH_ATTEMPTS = 2
RETENTION_DAYS = 14


def git_timeout() -> int:
    """读取可配置的 Git 超时，便于按部署环境调整。"""
    from django.conf import settings

    return int(getattr(settings, "BENCHMARK_SNAPSHOT_GIT_TIMEOUT", DEFAULT_GIT_TIMEOUT))


class SnapshotError(Exception):
    """快照不可用（不可恢复，需换样本或修正数据）。"""


class SnapshotRejected(SnapshotError):
    """输入不满足安全约束。"""


def snapshot_root() -> Path:
    from .benchmark_prepare import data_root

    return (data_root() / "_snapshots").resolve()


def validate_repo(repo: str) -> str:
    value = str(repo or "").strip()
    if not REPO_PATTERN.match(value) or ".." in value:
        raise SnapshotRejected(f"仓库标识不合法：{repo}")
    return value


def validate_sha(sha: str) -> str:
    value = str(sha or "").strip().lower()
    if not SHA_PATTERN.match(value):
        raise SnapshotRejected(f"Commit 不合法：{sha}")
    return value


def is_allowed_pair(repo: str, base_sha: str, head_sha: str) -> bool:
    """只有出现在已发布样本里的仓库 + Commit 组合才允许取快照。"""
    return BenchmarkCase.objects.filter(
        repo=repo, base_sha=base_sha, head_sha=head_sha, conversion_status="ok",
    ).exists()


def snapshot_dir(repo: str, base_sha: str, head_sha: str) -> Path:
    root = snapshot_root()
    slug = repo.replace("/", "__")
    target = (root / slug / f"{base_sha[:12]}-{head_sha[:12]}").resolve()
    if root not in target.parents:
        raise SnapshotRejected("快照路径越界")
    return target


def _git(args: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> str:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true", "GIT_LFS_SKIP_SMUDGE": "1"})
    result = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout or git_timeout(), env=env,
    )
    if result.returncode:
        raise SnapshotError((result.stderr or "").strip()[-800:] or "git 命令失败")
    return result.stdout


def _directory_size(path: Path) -> tuple[int, int]:
    total = files = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            continue
        if item.is_file():
            files += 1
            total += item.stat().st_size
            if total > MAX_SNAPSHOT_BYTES or files > MAX_SNAPSHOT_FILES:
                break
    return total, files


def _materialize(repo: str, base_sha: str, head_sha: str, target: Path) -> tuple[int, int]:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=False)
    _git(["init", "--quiet"], cwd=target)
    _git(["remote", "add", "origin", f"{GITHUB_BASE}/{repo}.git"], cwd=target)
    _git(["config", "core.sparseCheckout", "false"], cwd=target)
    fetch_args = [
        "fetch", "--quiet", "--force", "--no-tags", "--depth=50", "origin",
        f"{base_sha}:refs/bench/base", f"{head_sha}:refs/bench/head",
    ]
    for attempt in range(1, GIT_FETCH_ATTEMPTS + 1):
        try:
            _git(fetch_args, cwd=target)
            break
        except subprocess.TimeoutExpired:
            if attempt >= GIT_FETCH_ATTEMPTS:
                raise
            # 半截的 pack 会让下一次 fetch 直接报 bad pack header，先清干净再试。
            shutil.rmtree(target / ".git", ignore_errors=True)
            _git(["init", "--quiet"], cwd=target)
            _git(["remote", "add", "origin", f"{GITHUB_BASE}/{repo}.git"], cwd=target)
    _git(["checkout", "--quiet", "--force", "--detach", "refs/bench/head"], cwd=target)
    size, files = _directory_size(target)
    if size > MAX_SNAPSHOT_BYTES:
        raise SnapshotError(f"快照体积超过上限：{size} 字节")
    if files > MAX_SNAPSHOT_FILES:
        raise SnapshotError(f"快照文件数超过上限：{files}")
    # 只读：审查过程绝不写入被审查代码
    for item in target.iterdir():
        try:
            os.chmod(item, 0o555 if item.is_dir() else 0o444)
        except OSError:
            pass
    return size, files


def ensure_snapshot(repo: str, base_sha: str, head_sha: str, *, record=None) -> BenchmarkRepositorySnapshot:
    """获取或复用快照；失败写入确定状态与原因，供预检和重试使用。"""
    repo, base_sha, head_sha = validate_repo(repo), validate_sha(base_sha), validate_sha(head_sha)
    if record is None and not is_allowed_pair(repo, base_sha, head_sha):
        raise SnapshotRejected("该仓库与 Commit 组合不在已发布样本中")
    path = snapshot_dir(repo, base_sha, head_sha)
    with transaction.atomic():
        snapshot, _ = BenchmarkRepositorySnapshot.objects.select_for_update().get_or_create(
            repo=repo, base_sha=base_sha, head_sha=head_sha,
            defaults={"path": str(path)},
        )
        if snapshot.status == "ready" and path.exists() and (path / ".git").exists():
            snapshot.last_used_at = timezone.now()
            snapshot.save(update_fields=["last_used_at"])
            return snapshot
        snapshot.status = "pending"
        snapshot.path = str(path)
        snapshot.save(update_fields=["status", "path"])
    try:
        size, files = _materialize(repo, base_sha, head_sha, path)
    except (SnapshotError, subprocess.TimeoutExpired, OSError) as exc:
        snapshot.status = "failed"
        snapshot.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        snapshot.save(update_fields=["status", "error_message"])
        shutil.rmtree(path, ignore_errors=True)
        raise SnapshotError(snapshot.error_message) from exc
    snapshot.status = "ready"
    snapshot.size_bytes = size
    snapshot.file_count = files
    snapshot.error_message = ""
    snapshot.last_used_at = timezone.now()
    snapshot.save(update_fields=["status", "size_bytes", "file_count", "error_message", "last_used_at"])
    return snapshot


@contextmanager
def snapshot_lease(snapshots):
    """运行期租约：期间的快照不会被清理。"""
    ids = [item.pk for item in snapshots if item is not None]
    if ids:
        BenchmarkRepositorySnapshot.objects.filter(pk__in=ids).update(
            lease_count=F("lease_count") + 1, last_used_at=timezone.now(),
        )
    try:
        yield
    finally:
        for pk in ids:
            BenchmarkRepositorySnapshot.objects.filter(pk=pk, lease_count__gt=0).update(
                lease_count=F("lease_count") - 1,
            )


def purge_snapshots(*, retention_days: int = RETENTION_DAYS, dry_run: bool = True) -> dict[str, int]:
    """清理过期且无租约的快照；被运行引用的、未过期的都不动。"""
    cutoff = timezone.now() - timedelta(days=retention_days)
    candidates = BenchmarkRepositorySnapshot.objects.filter(
        status__in=["ready", "failed"], lease_count=0,
    ).filter(last_used_at__lt=cutoff)
    removed = 0
    bytes_freed = 0
    for snapshot in candidates:
        path = Path(snapshot.path or "")
        size = snapshot.size_bytes
        if not dry_run:
            if path.exists() and snapshot_root() in path.resolve().parents:
                shutil.rmtree(path, ignore_errors=True)
            snapshot.status = "purged"
            snapshot.save(update_fields=["status"])
        removed += 1
        bytes_freed += size
    return {"candidates": candidates.count(), "removed": removed, "bytes_freed": bytes_freed, "dry_run": dry_run}
