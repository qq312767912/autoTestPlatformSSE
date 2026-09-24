import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("wharttest_host_sync.py")
SPEC = importlib.util.spec_from_file_location("wharttest_host_sync", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ManagedBlockTests(unittest.TestCase):
    def test_adds_block_without_changing_existing_content(self):
        original = "127.0.0.1 localhost\n10.0.0.2 custom.local\n"
        block = MODULE.render_managed_block([
            {"hostname": "b.example.com", "ipv4": "10.0.0.4"},
            {"hostname": "a.example.com", "ipv4": "10.0.0.3"},
        ])
        updated = MODULE.replace_managed_block(original, block)
        self.assertIn("10.0.0.2 custom.local", updated)
        self.assertLess(updated.index("a.example.com"), updated.index("b.example.com"))

    def test_replaces_only_managed_block(self):
        original = "before\n# BEGIN WHARTTEST MANAGED HOSTS\n10.0.0.1 old.example.com\n# END WHARTTEST MANAGED HOSTS\nafter\n"
        block = MODULE.render_managed_block([{"hostname": "new.example.com", "ipv4": "10.0.0.9"}])
        updated = MODULE.replace_managed_block(original, block)
        self.assertEqual(updated.splitlines()[0], "before")
        self.assertEqual(updated.splitlines()[-1], "after")
        self.assertNotIn("old.example.com", updated)

    def test_rejects_incomplete_markers(self):
        with self.assertRaises(ValueError):
            MODULE.replace_managed_block("# BEGIN WHARTTEST MANAGED HOSTS\n", "block")

    def test_verifies_checksum(self):
        mappings = [{"hostname": "a.example.com", "ipv4": "10.0.0.3"}]
        import hashlib
        checksum = hashlib.sha256(MODULE.canonical_payload(mappings).encode()).hexdigest()
        MODULE.verify_checksum(mappings, checksum)
        with self.assertRaises(ValueError):
            MODULE.verify_checksum(mappings, "0" * 64)

    def test_agent_token_can_come_from_environment(self):
        with mock.patch.dict(os.environ, {
            "TEST_HOST_SYNC_TOKEN_FILE": "/missing/token",
            "TEST_HOST_SYNC_TOKEN": "local-token",
        }, clear=False):
            self.assertEqual(MODULE.HostSyncAgent()._token(), "local-token")

    def test_container_only_mode_disables_host_update(self):
        with mock.patch.dict(os.environ, {"HOST_SYNC_APPLY_HOST": "false"}, clear=False):
            self.assertFalse(MODULE.HostSyncAgent().apply_host_enabled)


if __name__ == "__main__":
    unittest.main()
