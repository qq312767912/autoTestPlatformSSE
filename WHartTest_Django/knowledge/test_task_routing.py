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
            self.assertIn('--pool=solo --concurrency=1', command)
            queue = command.split(' -Q ')[1].split()[0]
            self.assertNotIn(',', queue)
            self.assertNotIn(queue, consumers)
            consumers[queue] = section
        for task, queue in expected.items():
            routed = app.amqp.router.route({}, task)
            self.assertEqual(routed['queue'].name, queue, task)
            self.assertIn(queue, consumers, task)
        app.close()
