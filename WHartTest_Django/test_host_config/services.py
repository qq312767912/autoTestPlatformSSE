import hashlib
import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import TestHostConfigState, TestHostConfigVersion, TestHostMapping


def canonical_snapshot(mappings=None):
    queryset = mappings if mappings is not None else TestHostMapping.objects.filter(enabled=True)
    rows = [
        {
            "system_name": item.system_name,
            "hostname": item.hostname,
            "ipv4": item.ipv4,
            "remark": item.remark,
        }
        for item in queryset
        if item.enabled
    ]
    return sorted(rows, key=lambda row: row["hostname"])


def snapshot_checksum(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bump_draft_revision():
    with transaction.atomic():
        state = TestHostConfigState.objects.select_for_update().filter(singleton_key=1).first()
        if state is None:
            state = TestHostConfigState.objects.create(singleton_key=1, draft_revision=1)
        else:
            state.draft_revision += 1
            state.save(update_fields=["draft_revision", "updated_at"])
        return state.draft_revision


def build_diff():
    state = TestHostConfigState.get_state()
    current = canonical_snapshot()
    published = state.published_version.snapshot if state.published_version_id else []
    current_by_host = {item["hostname"]: item for item in current}
    published_by_host = {item["hostname"]: item for item in published}
    added = [current_by_host[key] for key in sorted(current_by_host.keys() - published_by_host.keys())]
    removed = [published_by_host[key] for key in sorted(published_by_host.keys() - current_by_host.keys())]
    changed = [
        {"before": published_by_host[key], "after": current_by_host[key]}
        for key in sorted(current_by_host.keys() & published_by_host.keys())
        if current_by_host[key] != published_by_host[key]
    ]
    return {"added": added, "changed": changed, "removed": removed, "count": len(added) + len(changed) + len(removed)}


def _create_version(*, snapshot, revision, user, source_version=None):
    next_version = (TestHostConfigVersion.objects.aggregate(value=Max("version"))["value"] or 0) + 1
    return TestHostConfigVersion.objects.create(
        version=next_version,
        source_draft_revision=revision,
        snapshot=snapshot,
        checksum=snapshot_checksum(snapshot),
        status="published",
        source_version=source_version,
        created_by=user,
        published_at=timezone.now(),
    )


def publish(*, expected_revision, user):
    with transaction.atomic():
        state = TestHostConfigState.objects.select_for_update().filter(singleton_key=1).first()
        if state is None:
            state = TestHostConfigState.objects.create(singleton_key=1)
        if state.draft_revision != expected_revision:
            raise ValueError("配置已被其他用户修改，请刷新后重新发布")
        snapshot = canonical_snapshot()
        previous = state.published_version
        version = _create_version(snapshot=snapshot, revision=state.draft_revision, user=user)
        if previous and previous.status == "published":
            previous.status = "superseded"
            previous.save(update_fields=["status"])
        state.published_version = version
        state.save(update_fields=["published_version", "updated_at"])
        return version


def rollback(*, source_version, user):
    with transaction.atomic():
        state = TestHostConfigState.objects.select_for_update().filter(singleton_key=1).first()
        if state is None:
            state = TestHostConfigState.objects.create(singleton_key=1)
        TestHostMapping.objects.all().delete()
        TestHostMapping.objects.bulk_create([
            TestHostMapping(
                system_name=item["system_name"], hostname=item["hostname"], ipv4=item["ipv4"],
                enabled=True, remark=item.get("remark", ""), created_by=user, updated_by=user,
            )
            for item in source_version.snapshot
        ])
        state.draft_revision += 1
        previous = state.published_version
        version = _create_version(
            snapshot=source_version.snapshot,
            revision=state.draft_revision,
            user=user,
            source_version=source_version,
        )
        if previous and previous.status == "published":
            previous.status = "superseded"
            previous.save(update_fields=["status"])
        state.published_version = version
        state.save(update_fields=["published_version", "draft_revision", "updated_at"])
        return version
