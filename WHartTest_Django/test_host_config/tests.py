import json
import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import TestHostConfigState, TestHostConfigVersion, TestHostMapping
from .services import build_diff, publish, rollback, snapshot_checksum
from .validators import normalize_hostname, validate_safe_ipv4


User = get_user_model()


class ValidatorTests(TestCase):
    def test_normalizes_hostname(self):
        self.assertEqual(normalize_hostname("WWW.Example.COM."), "www.example.com")

    def test_rejects_unsafe_ipv4(self):
        for value in ["127.0.0.1", "169.254.1.1", "224.0.0.1", "0.0.0.0"]:
            with self.assertRaises(Exception):
                validate_safe_ipv4(value)


class PublishServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("host-admin", password="x")
        self.mapping = TestHostMapping.objects.create(
            system_name="交易系统", hostname="trade.internal.example", ipv4="10.10.20.30",
            created_by=self.user, updated_by=self.user,
        )
        state = TestHostConfigState.get_state()
        state.draft_revision = 1
        state.save(update_fields=["draft_revision"])

    def test_publish_is_stable_and_rollback_creates_new_version(self):
        version = publish(expected_revision=1, user=self.user)
        self.assertEqual(version.snapshot[0]["hostname"], "trade.internal.example")
        self.assertEqual(version.checksum, snapshot_checksum(version.snapshot))
        rolled_back = rollback(source_version=version, user=self.user)
        self.assertGreater(rolled_back.version, version.version)
        self.assertEqual(rolled_back.snapshot, version.snapshot)
        self.assertEqual(rolled_back.source_version, version)

    def test_publish_rejects_stale_revision(self):
        with self.assertRaisesMessage(ValueError, "配置已被"):
            publish(expected_revision=0, user=self.user)

    def test_diff_detects_change(self):
        publish(expected_revision=1, user=self.user)
        self.mapping.ipv4 = "10.10.20.31"
        self.mapping.save()
        self.assertEqual(build_diff()["count"], 1)


class AgentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token_file = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
        self.token_file.write("agent-test-secret")
        self.token_file.close()
        self.previous = os.environ.get("TEST_HOST_SYNC_TOKEN_FILE")
        os.environ["TEST_HOST_SYNC_TOKEN_FILE"] = self.token_file.name

    def tearDown(self):
        os.unlink(self.token_file.name)
        if self.previous is None:
            os.environ.pop("TEST_HOST_SYNC_TOKEN_FILE", None)
        else:
            os.environ["TEST_HOST_SYNC_TOKEN_FILE"] = self.previous

    def test_export_requires_agent_token(self):
        response = self.client.get("/api/test-host-config/agent/export/")
        self.assertEqual(response.status_code, 403)

    def test_export_returns_published_snapshot_and_etag(self):
        version = TestHostConfigVersion.objects.create(
            version=1, source_draft_revision=0,
            snapshot=[{"system_name": "A", "hostname": "a.example.com", "ipv4": "10.0.0.8", "remark": ""}],
            checksum="a" * 64, status="published",
        )
        state = TestHostConfigState.get_state()
        state.published_version = version
        state.save(update_fields=["published_version"])
        response = self.client.get(
            "/api/test-host-config/agent/export/",
            HTTP_AUTHORIZATION="Bearer agent-test-secret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)
        self.assertEqual(response["ETag"], '"' + "a" * 64 + '"')

    def test_report_rejects_arbitrary_container_name(self):
        response = self.client.post(
            "/api/test-host-config/agent/report/",
            {"nodes": [{
                "node_id": "database", "node_type": "backend", "display_name": "bad",
                "applied_version": 1, "applied_checksum": "a" * 64, "status": "synced",
            }]},
            format="json",
            HTTP_AUTHORIZATION="Bearer agent-test-secret",
        )
        self.assertEqual(response.status_code, 400)
