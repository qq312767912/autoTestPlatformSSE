import unittest
from unittest.mock import AsyncMock, Mock
from consumer import TaskConsumer
from models import QueueModel, SocketDataModel, UiSocketEnum


class TaskIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_receiving_another_task_preserves_running_identity(self):
        consumer = TaskConsumer.__new__(TaskConsumer)
        consumer._current_user = 'running-user'
        consumer.add_task = AsyncMock()
        task = QueueModel(func_name=UiSocketEnum.TEST_CASE, func_args={'task_token': 'new-token'})
        await consumer.handle_message(SocketDataModel(user='queued-user', data=task))
        self.assertEqual(consumer._current_user, 'running-user')
        self.assertEqual(task.user, 'queued-user')

    async def test_task_token_and_user_stay_together_and_token_is_cleared(self):
        consumer = TaskConsumer.__new__(TaskConsumer)
        consumer.ws_client = Mock()
        consumer.execute_page_steps = AsyncMock()
        consumer.execute_test_case = AsyncMock(side_effect=RuntimeError('failed'))
        consumer.execute_batch = AsyncMock()
        consumer.stop_execution = AsyncMock()
        consumer.apply_config_update = AsyncMock()
        task = QueueModel(func_name=UiSocketEnum.TEST_CASE, func_args={'task_token': 'token-a'}, user='user-a')
        with self.assertRaises(RuntimeError):
            await consumer._route_task(task)
        self.assertEqual(consumer._current_user, 'user-a')
        self.assertEqual(consumer.ws_client.set_task_token.call_args_list[0].args, ('token-a',))
        consumer.ws_client.set_task_token.assert_called_with(None)
