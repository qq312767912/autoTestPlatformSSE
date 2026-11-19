import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .web_runtime import LoopWatchdog, WatchedApplication


class WebRuntimeTests(unittest.TestCase):
    def test_stall_dumps_threads_before_exiting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stall.log'
            exits = []
            watchdog = LoopWatchdog(10, str(path), exits.append)
            with patch('wharttest_django.web_runtime.time.monotonic', return_value=watchdog.last_tick + 11):
                self.assertTrue(watchdog.check())
            self.assertEqual(exits, [70])
            self.assertIn('loop stalled', path.read_text())
            self.assertIn('test_stall_dumps_threads_before_exiting', path.read_text())

    def test_active_or_stopped_loop_does_not_exit(self):
        exits = []
        watchdog = LoopWatchdog(10, exit_process=exits.append)
        self.assertFalse(watchdog.check())
        watchdog.stopped.set()
        watchdog.last_tick -= 100
        self.assertFalse(watchdog.check())
        self.assertEqual(exits, [])

    def test_lifespan_starts_and_cleans_up(self):
        async def exercise():
            events = iter([{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}])
            sent = []
            async def receive():
                await asyncio.sleep(0)
                return next(events)
            async def send(value):
                sent.append(value)
            async def app(*args):
                self.fail('Django must not receive lifespan events')
            await WatchedApplication(app)({'type': 'lifespan'}, receive, send)
            self.assertEqual(sent, [{'type': 'lifespan.startup.complete'}, {'type': 'lifespan.shutdown.complete'}])
        asyncio.run(exercise())

    def test_http_and_websocket_are_forwarded(self):
        async def exercise():
            scopes = []
            async def app(scope, receive, send):
                scopes.append(scope['type'])
            wrapped = WatchedApplication(app)
            for kind in ('http', 'websocket'):
                await wrapped({'type': kind}, None, None)
            self.assertEqual(scopes, ['http', 'websocket'])
        asyncio.run(exercise())

    def test_blocked_event_loop_is_terminated_with_traceback(self):
        import subprocess
        import sys
        module = Path(__file__).with_name('web_runtime.py')
        with tempfile.TemporaryDirectory() as directory:
            dump = str(Path(directory) / 'blocked.log')
            script = f"""
import importlib.util, asyncio, time
spec=importlib.util.spec_from_file_location('runtime', {str(module)!r})
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
async def run():
 w=m.LoopWatchdog(0.3, {dump!r})
 import threading
 threading.Thread(target=w.monitor,daemon=True).start()
 time.sleep(10)
asyncio.run(run())
"""
            result = subprocess.run([sys.executable, '-c', script], timeout=5, capture_output=True)
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertIn('loop stalled', Path(dump).read_text())
