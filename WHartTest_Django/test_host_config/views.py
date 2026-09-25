from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import TestHostConfigState, TestHostConfigVersion, TestHostDiagnosis, TestHostMapping, TestHostNodeStatus
from .permissions import AgentTokenPermission, TestHostConfigPermission
from .serializers import (
    AgentReportSerializer,
    TestHostConfigVersionSerializer,
    TestHostDiagnosisSerializer,
    TestHostMappingSerializer,
    TestHostNodeStatusSerializer,
)
from .services import build_diff, bump_draft_revision, publish, rollback
from .tasks import run_host_diagnosis


class TestHostMappingViewSet(viewsets.ModelViewSet):
    queryset = TestHostMapping.objects.select_related("created_by", "updated_by").all()
    serializer_class = TestHostMappingSerializer
    permission_classes = [IsAuthenticated, TestHostConfigPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.request.query_params.get("search", "").strip()
        enabled = self.request.query_params.get("enabled", "").strip().lower()
        if query:
            queryset = queryset.filter(Q(system_name__icontains=query) | Q(hostname__icontains=query) | Q(ipv4__icontains=query))
        if enabled in {"true", "false"}:
            queryset = queryset.filter(enabled=enabled == "true")
        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save(created_by=self.request.user, updated_by=self.request.user)
            bump_draft_revision()

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save(updated_by=self.request.user)
            bump_draft_revision()

    def perform_destroy(self, instance):
        with transaction.atomic():
            super().perform_destroy(instance)
            bump_draft_revision()

    @action(detail=False, methods=["post"], url_path="bulk-import")
    def bulk_import(self, request):
        text = str(request.data.get("text") or "")
        overwrite = request.data.get("overwrite", False) is True
        dry_run = request.data.get("dry_run", True) is not False
        system_name = str(request.data.get("system_name") or "批量导入").strip()[:100] or "批量导入"
        rows, errors, seen = [], [], set()
        from django.core.exceptions import ValidationError
        from .validators import normalize_hostname, validate_safe_ipv4
        for line_number, raw in enumerate(text.splitlines(), 1):
            body = raw.split("#", 1)[0].strip()
            if not body:
                continue
            parts = body.split()
            if len(parts) < 2:
                errors.append({"line": line_number, "text": raw, "detail": "格式应为：IPv4 域名"})
                continue
            try:
                ipv4 = validate_safe_ipv4(parts[0])
                hostnames = [normalize_hostname(value) for value in parts[1:]]
            except ValidationError as exc:
                errors.append({"line": line_number, "text": raw, "detail": "; ".join(exc.messages)})
                continue
            for hostname in hostnames:
                if hostname in seen:
                    errors.append({"line": line_number, "text": raw, "detail": f"域名 {hostname} 在导入内容中重复"})
                    continue
                seen.add(hostname)
                existing = TestHostMapping.objects.filter(hostname=hostname).first()
                rows.append({
                    "line": line_number, "hostname": hostname, "ipv4": ipv4,
                    "action": "unchanged" if existing and existing.ipv4 == ipv4 else "update" if existing else "create",
                    "existing_ipv4": existing.ipv4 if existing else None,
                })
        conflicts = [row for row in rows if row["action"] == "update"]
        if dry_run or errors or (conflicts and not overwrite):
            return Response({"rows": rows, "errors": errors, "conflicts": conflicts, "can_import": not errors and (overwrite or not conflicts)})
        changed = 0
        with transaction.atomic():
            for row in rows:
                if row["action"] == "unchanged":
                    continue
                mapping, created = TestHostMapping.objects.get_or_create(
                    hostname=row["hostname"],
                    defaults={"ipv4": row["ipv4"], "system_name": system_name, "enabled": True,
                              "remark": "批量导入", "updated_by": request.user, "created_by": request.user},
                )
                if not created:
                    mapping.ipv4 = row["ipv4"]
                    mapping.system_name = system_name
                    mapping.enabled = True
                    mapping.remark = "批量导入"
                    mapping.updated_by = request.user
                    mapping.save(update_fields=["ipv4", "system_name", "enabled", "remark", "updated_by", "updated_at"])
                changed += 1
            if changed:
                bump_draft_revision()
        return Response({"created_or_updated": changed, "unchanged": len(rows) - changed, "errors": []}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def overview(self, request):
        state = TestHostConfigState.get_state()
        version = state.published_version
        nodes = list(TestHostNodeStatus.objects.all())
        counts = {"synced": 0, "pending": 0, "failed": 0, "offline": 0}
        for node in nodes:
            effective = "offline" if (timezone.now() - node.last_seen_at).total_seconds() > 90 else node.status
            if effective == "synced" and version and node.applied_version != version.version:
                effective = "pending"
            counts[effective] += 1
        return Response({
            "draft_revision": state.draft_revision,
            "published_version": version.version if version else None,
            "published_checksum": version.checksum if version else "",
            "published_at": version.published_at if version else None,
            "published_by": version.created_by.username if version and version.created_by else "",
            "pending_changes": build_diff()["count"],
            "node_counts": counts,
        })

    @action(detail=False, methods=["get"])
    def diff(self, request):
        return Response(build_diff())

    @action(detail=False, methods=["post"])
    def publish(self, request):
        try:
            expected = int(request.data.get("expected_draft_revision"))
        except (TypeError, ValueError):
            return Response({"detail": "缺少有效的草稿修订号"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            version = publish(expected_revision=expected, user=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(TestHostConfigVersionSerializer(version).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def versions(self, request):
        queryset = TestHostConfigVersion.objects.select_related("created_by", "source_version").all()[:100]
        return Response(TestHostConfigVersionSerializer(queryset, many=True).data)

    @action(detail=True, methods=["post"])
    def diagnose(self, request, pk=None):
        mapping = self.get_object()
        diagnosis = TestHostDiagnosis.objects.create(mapping=mapping, requested_by=request.user)
        run_host_diagnosis.delay(diagnosis.id)
        return Response(TestHostDiagnosisSerializer(diagnosis).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path=r"diagnoses/(?P<diagnosis_id>[^/.]+)")
    def diagnosis_detail(self, request, diagnosis_id=None):
        diagnosis = TestHostDiagnosis.objects.select_related("mapping").get(pk=diagnosis_id)
        return Response(TestHostDiagnosisSerializer(diagnosis).data)

    @action(detail=False, methods=["get"])
    def nodes(self, request):
        return Response(TestHostNodeStatusSerializer(TestHostNodeStatus.objects.all(), many=True).data)


class TestHostVersionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TestHostConfigVersion.objects.select_related("created_by", "source_version").all()
    serializer_class = TestHostConfigVersionSerializer
    permission_classes = [IsAuthenticated, TestHostConfigPermission]

    @action(detail=True, methods=["post"])
    def rollback(self, request, pk=None):
        version = rollback(source_version=self.get_object(), user=request.user)
        return Response(self.get_serializer(version).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AgentTokenPermission])
def agent_export(request):
    state = TestHostConfigState.get_state()
    version = state.published_version
    if not version:
        return HttpResponse(status=status.HTTP_204_NO_CONTENT)
    etag = f'"{version.checksum}"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponse(status=status.HTTP_304_NOT_MODIFIED)
    response = JsonResponse({"version": version.version, "checksum": version.checksum, "mappings": version.snapshot})
    response["ETag"] = etag
    return response


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AgentTokenPermission])
def agent_report(request):
    serializer = AgentReportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    now = timezone.now()
    for item in serializer.validated_data["nodes"]:
        TestHostNodeStatus.objects.update_or_create(
            node_id=item["node_id"],
            defaults={**item, "message": item.get("message", ""), "details": item.get("details", {}), "last_seen_at": now},
        )
    return JsonResponse({"accepted": len(serializer.validated_data["nodes"])})
