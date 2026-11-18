import json
from unittest.mock import patch
from django.test import SimpleTestCase
from knowledge_evolution.ui_generation_jobs import consume_stream_result


class HealingFeedbackTests(SimpleTestCase):
    async def stream(self, *events):
        payload = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        payload += 'data: [DONE]\n\n'
        # 模拟网络分片，不依赖一个 chunk 恰好是一条事件。
        for start in range(0, len(payload), 7):
            yield payload[start:start + 7].encode()

    async def test_stream_error_propagates_reason(self):
        with self.assertRaisesMessage(ValueError, '模型调用失败'):
            await consume_stream_result(self.stream({'type': 'error', 'message': '模型调用失败'}))

    async def test_stopped_session_cancels_parent(self):
        with patch('knowledge_evolution.ui_generation_jobs.update_run') as update:
            with self.assertRaisesMessage(ValueError, '已停止'):
                await consume_stream_result(self.stream({'type': 'complete', 'status': 'stopped'}), 12)
        update.assert_called_once_with(12, cancel_requested=True)

    async def test_missing_terminal_result_is_failure(self):
        with self.assertRaisesMessage(ValueError, '未收到完成结果'):
            await consume_stream_result(self.stream({'type': 'token', 'content': 'hello'}))

    async def test_normal_completion(self):
        await consume_stream_result(self.stream({'type': 'complete'}))
