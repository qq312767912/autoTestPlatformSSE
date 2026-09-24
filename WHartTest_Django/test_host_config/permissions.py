import hmac
import os
from pathlib import Path

from rest_framework.permissions import BasePermission


ACTION_PERMISSIONS = {
    "publish": "test_host_config.publish_testhostconfig",
    "rollback": "test_host_config.rollback_testhostconfig",
    "diagnose": "test_host_config.diagnose_testhostmapping",
}


class TestHostConfigPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        action = getattr(view, "action", None)
        if action in ACTION_PERMISSIONS:
            return user.has_perm(ACTION_PERMISSIONS[action])
        model_permission = {
            "list": "view", "retrieve": "view", "overview": "view", "diff": "view",
            "versions": "view", "nodes": "view", "diagnosis_detail": "view",
            "create": "add", "update": "change", "partial_update": "change", "destroy": "delete",
        }.get(action, "view")
        return user.has_perm(f"test_host_config.{model_permission}_testhostmapping")


def _read_agent_token():
    token_file = os.environ.get("TEST_HOST_SYNC_TOKEN_FILE", "/run/secrets/test_host_sync_token")
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("TEST_HOST_SYNC_TOKEN", "").strip()


class AgentTokenPermission(BasePermission):
    def has_permission(self, request, view):
        configured = _read_agent_token()
        header = request.headers.get("Authorization", "")
        supplied = header[7:].strip() if header.startswith("Bearer ") else ""
        return bool(configured and supplied and hmac.compare_digest(configured, supplied))
