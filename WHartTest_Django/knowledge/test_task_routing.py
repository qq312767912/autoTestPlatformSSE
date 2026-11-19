"""Verify task dispatch and supervisor consumers agree on isolation."""
import configparser
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase
from celery import Celery


class TaskQueueIsolationTests(SimpleTestCase):
    def test_business_tasks_use_distinct_queues(self):
        app = Celery('routing-test', broker='memory://')
        app.conf.task_routes = settings.CELERY_TASK_ROUTES
        expected = {
            'ui_automation.run_ui_script_batch': 'ui_execution',
            'ui_automation.run_ui_healing': 'ui_execution',
            'knowledge.process_document': 'knowledge',
            'code_analysis.tasks.run_code_analysis': 'code_analysis',
            'code_analysis.tasks.retry_code_analysis_ocr': 'code_analysis',
            'code_analysis.tasks.retry_code_analysis_verification': 'code_analysis',
            'testcases.review_tasks.execute_testcase_review': 'testcase_review',
            'task_center.tasks.execute_scheduled_task': 'task_center',
            'requirements.execute_requirement_review': 'celery',
        }
        config = configparser.ConfigParser(interpolation=None)
        config.read(Path(settings.BASE_DIR) / 'supervisord.conf')
        consumers = {}
        for section in config.sections():
            command = config[section].get('command', '')
            if ' worker ' not in command:
                continue
            if section in ('program:celery_code_analysis', 'program:celery_testcase_review'):
                self.assertIn('--pool=solo --concurrency=1', command)
            else:
                self.assertIn('--pool=threads', command)
                import re
                self.assertGreater(int(re.search(r'--concurrency=(\d+)', command).group(1)), 1)
            queue = command.split(' -Q ')[1].split()[0]
            self.assertNotIn(',', queue)
            self.assertNotIn(queue, consumers)
            consumers[queue] = section
        for task, queue in expected.items():
            routed = app.amqp.router.route({}, task)
            self.assertEqual(routed['queue'].name, queue, task)
            self.assertIn(queue, consumers, task)
        app.close()

    def test_review_task_registered_without_loading_http_urls(self):
        from wharttest_django.celery import app
        app.loader.import_default_modules()
        app.autodiscover_tasks(force=True)
        self.assertIn('testcases.review_tasks.execute_testcase_review', app.tasks)

    def test_core_http_pool_preserves_stateful_single_worker(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(Path(settings.BASE_DIR) / 'supervisord.conf')
        self.assertIn('--port 8000 --workers=1', config['program:django']['command'])
        self.assertIn('--port 8001 --workers=3', config['program:django_core']['command'])

    def test_frontend_routes_only_safe_core_endpoints_to_pool(self):
        import re
        source = (Path(settings.BASE_DIR).parent / 'WHartTest_Vue/nginx.conf').read_text()
        pattern = re.search(r'location ~ (.+) \{', source).group(1)
        for path in ['/api/accounts/users/1/permissions/', '/api/auth/login-key/', '/api/token/', '/api/projects/', '/api/projects/31/', '/api/projects/31/statistics/']:
            self.assertIsNotNone(re.search(pattern, path), path)
        for path in ['/api/lg/chat/sessions/', '/api/orchestrator/agent-loop/', '/api/ui-automation/status/', '/api/projects/31/testcases/', '/ws/ui/actuator/']:
            self.assertIsNone(re.search(pattern, path), path)
