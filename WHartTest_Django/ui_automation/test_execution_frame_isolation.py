import asyncio
from unittest.mock import AsyncMock, Mock, patch
from django.test import SimpleTestCase
from .consumers import UiAutomationConsumer, SocketUserManager


class ExecutionFrameIsolationTests(SimpleTestCase):
    async def test_frames_stay_with_initiator_when_registry_replaced_or_removed(self):
        for replacement in (Mock(), None):
            consumer = UiAutomationConsumer()
            consumer._background_execution = False
            received = asyncio.Event()
            async def send(_message):
                received.set()
            consumer.send_json = AsyncMock(side_effect=send)
            session = Mock()
            session.take_latest_frame.return_value = {'data': 'test-frame', 'w': 100, 'h': 100}
            with patch.object(SocketUserManager, 'get_web_user', return_value=replacement) as lookup:
                task = asyncio.create_task(consumer._recorder_exec_frame_relay(session, 'same-user'))
                try:
                    await asyncio.wait_for(received.wait(), 1)
                finally:
                    task.cancel()
                    try: await task
                    except asyncio.CancelledError: pass
                lookup.assert_not_called()
            consumer.send_json.assert_awaited_once()

    async def test_background_execution_does_not_stream_frames(self):
        consumer = UiAutomationConsumer()
        consumer._background_execution = True
        consumer.send_json = AsyncMock()
        session = Mock()
        await consumer._recorder_exec_frame_relay(session, 'same-user')
        session.take_latest_frame.assert_not_called()
        consumer.send_json.assert_not_awaited()
