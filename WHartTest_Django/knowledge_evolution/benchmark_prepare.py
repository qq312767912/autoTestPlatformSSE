"""公开测评集数据准备：下载 → 校验 → 转换 → 原子发布。

设计要点（对应 R2/R6）：
- 下载使用 manifest 中登记的多源地址顺序回退，逐文件校验 SHA256 与字节数；
- 所有准备动作都在后台作业（Celery）内执行，绝不在 HTTP 请求里同步下载；
- 生产内网可改用运维预置的离线包：目录 + `manifest.json`，校验和必须与代码内 manifest 一致；
- 转换失败的样本写入 `BenchmarkCase.conversion_error` 与版本统计，不静默跳过；
- 版本发布是原子的：样本与统计全部写完后才把状态置为 ready。
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .benchmark_adapters import AdapterDependencyError, get_adapter
from .benchmark_catalog import CATALOG, manifest, sha256_file, version_label
from .benchmark_models import BenchmarkCase, BenchmarkDatasetVersion, BenchmarkPrepareJob

DOWNLOAD_TIMEOUT = 180
MAX_FILE_BYTES = 300 * 1024 * 1024
CHUNK = 1024 * 1024


def data_root() -> Path:
    configured = getattr(settings, "CODE_REVIEW_BENCHMARK_ROOT", "") or os.environ.get(
        "CODE_REVIEW_BENCHMARK_ROOT", ""
    )
    base = Path(configured) if configured else Path(settings.BASE_DIR) / "data" / "code-review-benchmarks"
    return base.resolve()


def raw_dir(benchmark_id: str, version: str | None = None) -> Path:
    return data_root() / "_raw" / benchmark_id / (version or version_label(benchmark_id))


def offline_dir(benchmark_id: str, version: str) -> Path:
    return data_root() / "_offline" / benchmark_id / version


def package_dir(benchmark_id: str, version: str) -> Path:
    return data_root() / "_packages" / benchmark_id / version


def _job_log(job: BenchmarkPrepareJob | None, message: str) -> None:
    if job is not None:
        job.append_log(message)


def _set_stage(job: BenchmarkPrepareJob | None, stage: str) -> None:
    if job is not None:
        job.stage = stage
        job.save(update_fields=["stage", "log"])


def _download(url: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "WHartTest-Benchmark/1.0"})
    written = 0
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, open(target, "wb") as handle:
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            written += len(block)
            if written > MAX_FILE_BYTES:
                raise ValueError(f"文件超过上限 {MAX_FILE_BYTES} 字节：{url}")
            handle.write(block)
    return written


def _extract_member(archive: Path, member_name: str, target: Path) -> int:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            normalized = member.name.split("/", 1)[-1] if "/" in member.name else member.name
            if normalized != member_name:
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with source, open(target, "wb") as handle:
                for block in iter(lambda: source.read(CHUNK), b""):
                    written += len(block)
                    if written > MAX_FILE_BYTES:
                        raise ValueError(f"解包文件超过上限：{member_name}")
                    handle.write(block)
            return written
    raise ValueError(f"压缩包内未找到 {member_name}")


def fetch_sources(benchmark_id: str, target_root: Path, job: BenchmarkPrepareJob | None = None) -> None:
    """按 manifest 顺序获取并校验全部原始文件；任一文件失败即整体失败。"""
    entry = manifest(benchmark_id)
    for item in entry["files"]:
        if item.get("excluded"):
            _job_log(job, f"跳过已排除文件：{item['name']}（上游语义未定义）")
            continue
        target = target_root / item["name"]
        if target.exists() and target.stat().st_size == item["size"] and sha256_file(str(target)) == item["sha256"]:
            _job_log(job, f"复用已校验文件：{item['name']}")
            continue
        last_error = ""
        for url in _file_urls(benchmark_id, item["name"]):
            try:
                _set_stage(job, f"download:{item['name']}")
                member = _archive_member(benchmark_id, item["name"])
                if member:
                    with tempfile.TemporaryDirectory() as temp:
                        archive = Path(temp) / "source.tar.gz"
                        written = _download(url, archive)
                        _job_log(job, f"已下载 {url}（{written} 字节），解出 {member}")
                        _extract_member(archive, member, target)
                else:
                    written = _download(url, target)
                    _job_log(job, f"已下载 {url}（{written} 字节）")
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                _job_log(job, f"源不可用，尝试下一个：{url} -> {last_error}")
        else:
            raise RuntimeError(f"全部下载源均失败（{item['name']}）：{last_error}")
        if not target.exists():
            raise RuntimeError(f"下载后文件缺失：{item['name']}")
        size = target.stat().st_size
        if item["size"] and size != item["size"]:
            raise RuntimeError(f"{item['name']} 字节数不符：期望 {item['size']}，实际 {size}")
        digest = sha256_file(str(target))
        if digest != item["sha256"]:
            raise RuntimeError(f"{item['name']} 校验和不符：期望 {item['sha256']}，实际 {digest}")
        _job_log(job, f"校验通过：{item['name']} sha256={digest[:16]}…")


def _file_urls(benchmark_id: str, file_name: str) -> list[str]:
    from .benchmark_catalog import resolve_files

    for item in resolve_files(benchmark_id):
        if item["name"] == file_name:
            return item["urls"]
    return []


def _archive_member(benchmark_id: str, file_name: str) -> str:
    for item in CATALOG[benchmark_id]["files"]:
        if item["name"] == file_name:
            return item.get("archive_member", "")
    return ""


def import_offline_package(benchmark_id: str, source: Path, job: BenchmarkPrepareJob | None = None) -> Path:
    """导入运维预置的同版本离线包；manifest 摘要或任一校验和不符即拒绝。"""
    source = Path(source)
    manifest_path = source / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"离线包缺少 manifest.json：{source}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest(benchmark_id)
    if payload.get("manifest_digest") != expected["manifest_digest"]:
        raise RuntimeError("离线包 manifest 摘要与代码内固定版本不一致，拒绝导入")
    if payload.get("version") != expected["version"]:
        raise RuntimeError(f"离线包版本不符：期望 {expected['version']}，实际 {payload.get('version')}")
    target_root = raw_dir(benchmark_id, expected["version"])
    target_root.mkdir(parents=True, exist_ok=True)
    for item in expected["files"]:
        if item.get("excluded"):
            continue
        candidate = source / item["name"]
        if not candidate.exists():
            raise RuntimeError(f"离线包缺少文件：{item['name']}")
        digest = sha256_file(str(candidate))
        if digest != item["sha256"]:
            raise RuntimeError(f"离线包 {item['name']} 校验和不符")
        shutil.copy2(candidate, target_root / item["name"])
        _job_log(job, f"离线包校验通过：{item['name']}")
    return target_root


def build_offline_package(benchmark_id: str, target: Path | None = None) -> Path:
    """把已校验的原始文件打成离线包，供内网环境预置。"""
    entry = manifest(benchmark_id)
    source_root = raw_dir(benchmark_id, entry["version"])
    target = Path(target or package_dir(benchmark_id, entry["version"]))
    target.mkdir(parents=True, exist_ok=True)
    for item in entry["files"]:
        if item.get("excluded"):
            continue
        candidate = source_root / item["name"]
        if not candidate.exists():
            raise RuntimeError(f"打包前缺少文件：{item['name']}")
        shutil.copy2(candidate, target / item["name"])
    (target / "manifest.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _publish_version(
    benchmark_id: str,
    *,
    origin: str,
    actor,
    job: BenchmarkPrepareJob | None,
    raw_root: Path,
) -> BenchmarkDatasetVersion:
    entry = manifest(benchmark_id)
    adapter = get_adapter(benchmark_id)
    if entry["adapter"]["version"] != adapter.version:
        raise RuntimeError("适配器版本与 manifest 不一致，请先更新 catalog")
    _set_stage(job, "convert")
    outcome = adapter.convert(str(raw_root))
    if not outcome.cases:
        raise RuntimeError(f"转换后无可用样本，拒绝发布；拒绝原因统计见作业日志")
    language_counts: dict[str, int] = {}
    rejection_summary: dict[str, int] = {}
    for case in outcome.cases:
        language_counts[case["language"]] = language_counts.get(case["language"], 0) + 1
    for rejection in outcome.rejections:
        reason = str(rejection.get("reason") or "unknown").split(":")[0]
        rejection_summary[reason] = rejection_summary.get(reason, 0) + 1
    gold_total = sum(len(case["gold_issues"]) for case in outcome.cases)
    clean_total = sum(1 for case in outcome.cases if case["is_clean"] is True)
    with transaction.atomic():
        version, _ = BenchmarkDatasetVersion.objects.get_or_create(
            benchmark_id=benchmark_id,
            version=entry["version"],
            defaults={
                "upstream_revision": entry["upstream"].get("revision", ""),
                "adapter_version": entry["adapter"]["version"],
                "manifest_digest": entry["manifest_digest"],
                "manifest": entry,
                "origin": origin,
                "created_by": actor if getattr(actor, "pk", None) else None,
            },
        )
        if version.status == "ready":
            _job_log(job, f"版本已存在且可运行，跳过重复发布：{version.version}")
            return version
        version.cases.all().delete()
        BenchmarkCase.objects.bulk_create([
            BenchmarkCase(
                version=version,
                benchmark_id=benchmark_id,
                upstream_instance_id=case["upstream_instance_id"],
                repo=case["repo"],
                base_sha=case["base_sha"],
                head_sha=case["head_sha"],
                language=case["language"],
                gold_issues=case["gold_issues"],
                gold_count=len(case["gold_issues"]),
                is_clean=case["is_clean"],
                is_clean_source=case["is_clean_source"],
                source_url=case["source_url"],
                conversion_status="ok",
                raw_ref=case["raw_ref"],
            ) for case in outcome.cases
        ], batch_size=500)
        rejects = [
            BenchmarkCase(
                version=version,
                benchmark_id=benchmark_id,
                upstream_instance_id=str(item["upstream_instance_id"]),
                conversion_status="rejected",
                conversion_error=f"{item.get('reason')} {item.get('detail') or ''}".strip()[:2000],
                raw_ref={"reason": item.get("reason")},
            ) for item in outcome.rejections
        ]
        if rejects:
            BenchmarkCase.objects.bulk_create(rejects, batch_size=500)
        version.case_total = outcome.stats.get("records", len(outcome.cases))
        version.case_valid = len(outcome.cases)
        version.case_rejected = len(outcome.rejections)
        version.gold_total = gold_total
        version.clean_total = clean_total
        version.language_counts = language_counts
        version.rejection_summary = rejection_summary
        version.status = "ready"
        version.error_message = ""
        version.published_at = timezone.now()
        version.save(update_fields=[
            "case_total", "case_valid", "case_rejected", "gold_total", "clean_total",
            "language_counts", "rejection_summary", "status", "error_message", "published_at",
        ])
    _job_log(
        job,
        f"发布完成：有效 {version.case_valid} / 拒绝 {version.case_rejected} / "
        f"金标 {gold_total} / 干净样本 {clean_total} / 语言 {language_counts}",
    )
    return version


def prepare_benchmark(
    benchmark_id: str,
    *,
    actor=None,
    job: BenchmarkPrepareJob | None = None,
    offline_package: str = "",
) -> BenchmarkDatasetVersion:
    """同步执行准备流程；调用方负责放进后台作业。"""
    if benchmark_id not in CATALOG:
        raise ValueError(f"未知公开测评集：{benchmark_id}")
    version = version_label(benchmark_id)
    origin = "offline_package" if offline_package else "network"
    _set_stage(job, "load")
    if offline_package:
        raw_root = import_offline_package(benchmark_id, Path(offline_package), job)
    else:
        raw_root = raw_dir(benchmark_id, version)
        raw_root.mkdir(parents=True, exist_ok=True)
        fetch_sources(benchmark_id, raw_root, job)
    return _publish_version(benchmark_id, origin=origin, actor=actor, job=job, raw_root=raw_root)


@shared_task(bind=True, max_retries=0)
def run_prepare_job(self, job_id: str) -> dict[str, Any]:
    job = BenchmarkPrepareJob.objects.get(pk=job_id)
    job.status = "running"
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        version = prepare_benchmark(
            job.benchmark_id,
            actor=job.requested_by,
            job=job,
            offline_package=job.offline_package,
        )
        job.version = version
        job.status = "succeeded"
        job.stage = "published"
        job.error_message = ""
        job.finished_at = timezone.now()
        job.save(update_fields=["version", "status", "stage", "error_message", "finished_at"])
        return {"version": version.version, "cases": version.case_valid}
    except AdapterDependencyError as exc:
        return _fail_job(job, "dependency_missing", str(exc))
    except Exception as exc:  # 记录原因，允许重试
        return _fail_job(job, "prepare_failed", f"{type(exc).__name__}: {exc}")


def _fail_job(job: BenchmarkPrepareJob, stage: str, message: str) -> dict[str, Any]:
    job.status = "failed"
    job.stage = stage
    job.error_message = message[:2000]
    job.finished_at = timezone.now()
    job.append_log(f"准备失败：{message}")
    job.save(update_fields=["status", "stage", "error_message", "finished_at"])
    return {"error": message}
