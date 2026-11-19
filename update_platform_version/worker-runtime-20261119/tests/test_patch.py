import configparser
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]


class LegacyPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'wharttest_django').mkdir()
        (self.root / 'supervisord.conf').write_bytes((BASE / 'tests/legacy-supervisord.conf').read_bytes())
        (self.root / 'frontend.conf').write_text('server {\n    location /api/ {\n        proxy_pass http://backend:8000;\n    }\n}\n')
        (self.root / 'wharttest_django/settings.py').write_text("ORIGINAL = True\nCELERY_IMPORTS = ('original.tasks',)\nCELERY_TASK_ROUTES = {'task_center.tasks.*': {'queue': 'task_center'}}\n")
        (self.root / 'wharttest_django/asgi.py').write_text('application = object()\n')

    def run_patch(self):
        return subprocess.run([sys.executable, str(BASE / 'patch_runtime.py'), str(self.root)], capture_output=True)

    def test_legacy_upgrade_preserves_settings_and_isolates_web_and_tasks(self):
        result = self.run_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = {}
        exec((self.root / 'wharttest_django/settings.py').read_text(), settings)
        self.assertTrue(settings['ORIGINAL'])
        self.assertEqual(settings['CELERY_IMPORTS'], ('original.tasks', 'testcases.review_tasks'))
        self.assertEqual(len(settings['CELERY_TASK_ROUTES']), 6)
        config = configparser.RawConfigParser()
        config.read(self.root / 'supervisord.conf')
        self.assertIn('--port 8000 --workers=1', config['program:django']['command'])
        self.assertIn('--port 8001 --workers=3', config['program:django_core']['command'])
        self.assertEqual(len([s for s in config.sections() if ' worker ' in config[s].get('command', '')]), 6)
        self.assertIn('http://backend:8001', (self.root / 'frontend.conf').read_text())
        self.assertNotEqual(self.run_patch().returncode, 0)

    def test_changed_target_is_rejected_without_writes(self):
        path = self.root / 'supervisord.conf'
        path.write_text(path.read_text() + '\n# unexpected local change\n')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertNotEqual(self.run_patch().returncode, 0)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))


if __name__ == '__main__':
    unittest.main()
