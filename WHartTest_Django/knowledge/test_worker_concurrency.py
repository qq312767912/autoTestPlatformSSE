"""用真实 Celery 消费者验证并行槽位与审查串行，不调用模型或生产 Broker。"""
import threading
from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.test import SimpleTestCase


class WorkerConcurrencyTests(SimpleTestCase):
    def exercise_pool(self, pool, concurrency):
        app = Celery('worker-concurrency-probe', broker='memory://', backend='cache+memory://')
        app.conf.update(task_default_queue='probe-'+pool, worker_prefetch_multiplier=1)
        lock = threading.Lock()
        release = threading.Event()
        ready = threading.Event()
        active = 0
        peak = 0

        @app.task(name='probe.'+pool)
        def probe():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == concurrency:
                    ready.set()
            try:
                if not release.wait(10):
                    raise RuntimeError('probe gate timeout')
                return True
            finally:
                with lock:
                    active -= 1

        try:
            with start_worker(app, pool=pool, concurrency=concurrency, perform_ping_check=False, shutdown_timeout=20):
                tasks = [probe.delay() for _ in range(4)]
                try:
                    self.assertTrue(ready.wait(8), '任务未达到配置并发')
                    with lock:
                        self.assertEqual(peak, concurrency)
                finally:
                    release.set()
                self.assertTrue(all(task.get(timeout=15, disable_sync_subtasks=False) for task in tasks))
                self.assertEqual(peak, concurrency)
        finally:
            release.set()
            app.close()

    def test_other_tasks_run_four_at_a_time(self):
        self.exercise_pool('threads', 4)

    def test_review_tasks_remain_serial(self):
        self.exercise_pool('solo', 1)
