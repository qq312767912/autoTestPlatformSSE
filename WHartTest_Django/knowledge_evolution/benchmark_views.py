"""内置公开测评集目录与准备接口。

- `GET  /evaluation-benchmarks/`                       三个固定目录项 + 版本与准备状态
- `GET  /evaluation-benchmarks/{benchmark_id}/`        单集详情（含对账与校验和）
- `POST /evaluation-benchmarks/{benchmark_id}/prepare/` 授权维护角色异步准备
- `GET  /evaluation-benchmarks/{benchmark_id}/preflight/` 运行前预检
- `GET  /evaluation-benchmarks/{benchmark_id}/versions/`  版本历史
"""

from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .benchmark_catalog import CATALOG, catalog_payload, manifest, version_label
from .benchmark_models import BenchmarkDatasetVersion, BenchmarkPrepareJob
from .benchmark_preflight import preflight
from .benchmark_prepare import run_prepare_job
from .benchmark_snapshots import purge_snapshots


def _version_payload(version: BenchmarkDatasetVersion | None) -> dict | None:
    if version is None:
        return None
    return {
        "version": version.version,
        "status": version.status,
        "origin": version.origin,
        "upstream_revision": version.upstream_revision,
        "adapter_version": version.adapter_version,
        "manifest_digest": version.manifest_digest,
        "case_total": version.case_total,
        "case_valid": version.case_valid,
        "case_rejected": version.case_rejected,
        "gold_total": version.gold_total,
        "clean_total": version.clean_total,
        "language_counts": version.language_counts,
        "rejection_summary": version.rejection_summary,
        "published_at": version.published_at,
        "error_message": version.error_message,
    }


def _job_payload(job: BenchmarkPrepareJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": str(job.pk),
        "status": job.status,
        "stage": job.stage,
        "origin": job.origin,
        "error_message": job.error_message,
        "attempt": job.attempt,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "log_tail": (job.log or "")[-1200:],
    }


def _entry_payload(benchmark_id: str, version: BenchmarkDatasetVersion | None, job: BenchmarkPrepareJob | None) -> dict:
    base = next(item for item in catalog_payload() if item["benchmark_id"] == benchmark_id)
    base["version_detail"] = _version_payload(version)
    base["prepare_job"] = _job_payload(job)
    base["runnable"] = bool(version and version.status == "ready" and version.case_valid > 0)
    if base["version_detail"] is None:
        base["state"] = "not_prepared"
        base["state_label"] = "未准备"
    elif version.status == "failed":
        base["state"] = "failed"
        base["state_label"] = "准备失败"
    elif base["runnable"]:
        base["state"] = "ready"
        base["state_label"] = "可运行"
    else:
        base["state"] = "preparing"
        base["state_label"] = "准备中"
    return base


def _latest_version(benchmark_id: str) -> BenchmarkDatasetVersion | None:
    return (
        BenchmarkDatasetVersion.objects.filter(benchmark_id=benchmark_id)
        .order_by("-created_at").first()
    )


def _latest_job(benchmark_id: str) -> BenchmarkPrepareJob | None:
    return (
        BenchmarkPrepareJob.objects.filter(benchmark_id=benchmark_id)
        .order_by("-created_at").first()
    )


class BenchmarkCatalogViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    lookup_field = "benchmark_id"

    def list(self, request):
        entries = []
        for benchmark_id in CATALOG:
            entries.append(_entry_payload(
                benchmark_id, _latest_version(benchmark_id), _latest_job(benchmark_id),
            ))
        return Response({
            "benchmarks": entries,
            "note": "公开集为全局只读资产：数据版本发布后不可改写；内部集仍按项目导入。",
        })

    def retrieve(self, request, benchmark_id=None):
        if benchmark_id not in CATALOG:
            raise NotFound("未知公开测评集")
        entry = _entry_payload(benchmark_id, _latest_version(benchmark_id), _latest_job(benchmark_id))
        entry["manifest"] = manifest(benchmark_id)
        return Response(entry)

    @action(detail=False, methods=["get"], url_path=r"(?P<benchmark_id>[^/.]+)/versions")
    def versions(self, request, benchmark_id=None):
        if benchmark_id not in CATALOG:
            raise NotFound("未知公开测评集")
        rows = BenchmarkDatasetVersion.objects.filter(benchmark_id=benchmark_id).order_by("-created_at")
        return Response({"versions": [_version_payload(item) for item in rows]})

    @action(detail=False, methods=["post"], url_path=r"(?P<benchmark_id>[^/.]+)/prepare")
    def prepare(self, request, benchmark_id=None):
        if benchmark_id not in CATALOG:
            raise NotFound("未知公开测评集")
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("仅授权维护角色可准备全局公开数据")
        running = BenchmarkPrepareJob.objects.filter(
            benchmark_id=benchmark_id, status__in=["queued", "running"],
        ).first()
        if running:
            return Response(_job_payload(running), status=status.HTTP_202_ACCEPTED)
        offline = str(request.data.get("offline_package") or "").strip()
        if offline and not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("离线包导入仅限授权维护角色")
        job = BenchmarkPrepareJob.objects.create(
            benchmark_id=benchmark_id,
            requested_version=version_label(benchmark_id),
            origin="offline_package" if offline else "network",
            offline_package=offline,
            requested_by=request.user,
        )
        run_prepare_job.delay(str(job.pk))
        return Response(_job_payload(job), status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"(?P<benchmark_id>[^/.]+)/prepare-jobs")
    def prepare_jobs(self, request, benchmark_id=None):
        if benchmark_id not in CATALOG:
            raise NotFound("未知公开测评集")
        rows = BenchmarkPrepareJob.objects.filter(benchmark_id=benchmark_id).order_by("-created_at")[:20]
        return Response({"jobs": [_job_payload(item) for item in rows]})

    @action(detail=False, methods=["get"], url_path=r"(?P<benchmark_id>[^/.]+)/preflight")
    def preflight_view(self, request, benchmark_id=None):
        if benchmark_id not in CATALOG:
            raise NotFound("未知公开测评集")
        version = _latest_version(benchmark_id)
        if version is None:
            raise ValidationError({"detail": "该测评集尚未准备，无法预检"})
        languages = [item for item in (request.query_params.get("languages") or "").split(",") if item]
        include_clean = request.query_params.get("include_clean")
        limit = request.query_params.get("limit")
        model_config = None
        from code_analysis.models import CodeAnalysisLLMConfig

        model_config = CodeAnalysisLLMConfig.objects.filter(is_active=True).exclude(encrypted_api_key="").first()
        return Response(preflight(
            version,
            languages=languages or None,
            include_clean=None if include_clean in (None, "") else include_clean.lower() == "true",
            limit=int(limit) if str(limit or "").isdigit() else None,
            model_ready=model_config is not None,
            model_label=getattr(model_config, "name", "") if model_config else "",
        ))

    @action(detail=False, methods=["post"], url_path="purge-snapshots")
    def purge(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("仅授权维护角色可清理快照缓存")
        dry_run = str(request.data.get("dry_run", "true")).lower() != "false"
        retention = request.data.get("retention_days", 14)
        return Response(purge_snapshots(retention_days=int(retention), dry_run=dry_run))
