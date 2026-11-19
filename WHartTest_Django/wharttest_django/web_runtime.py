"""ASGI lifespan 与事件循环停滞保护；只恢复故障 Web 进程。"""
import asyncio
import faulthandler
import logging
import os
from pathlib import Path
import threading
import time

logger = logging.getLogger(__name__)


class LoopWatchdog:
    def __init__(self, timeout=120, dump_path='/app/data/logs/web-stall.log', exit_process=os._exit):
        self.timeout = timeout
        self.dump_path = dump_path
        self.exit_process = exit_process
        self.last_tick = time.monotonic()
        self.stopped = threading.Event()

    def tick(self):
        self.last_tick = time.monotonic()

    def check(self):
        if self.stopped.is_set() or time.monotonic() - self.last_tick < self.timeout:
            return False
        # 即使磁盘写入失败，也必须恢复故障进程；不重启 Celery 或整个容器。
        try:
            path = Path(self.dump_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as output:
                output.write(f'\n{time.strftime("%Y-%m-%dT%H:%M:%S%z")} pid={os.getpid()} loop stalled > {self.timeout}s\n')
                output.flush()
                faulthandler.dump_traceback(file=output, all_threads=True)
        except Exception:
            logger.exception('Cannot write stalled Web process traceback')
        self.exit_process(70)
        return True

    def monitor(self):
        while not self.stopped.wait(min(5, self.timeout / 4)):
            if self.check():
                return


class WatchedApplication:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'lifespan':
            return await self.application(scope, receive, send)
        watchdog = None
        heartbeat = None
        try:
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    timeout = float(os.environ.get('WEB_STALL_TIMEOUT_SECONDS', '120'))
                    if timeout > 0:
                        watchdog = LoopWatchdog(timeout)
                        async def pulse():
                            while True:
                                watchdog.tick()
                                await asyncio.sleep(min(1, timeout / 4))
                        heartbeat = asyncio.create_task(pulse())
                        threading.Thread(target=watchdog.monitor, daemon=True, name='web-loop-watchdog').start()
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        finally:
            if watchdog:
                watchdog.stopped.set()
            if heartbeat:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
