from dataclasses import dataclass, replace
from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from .generation_history_guard import GenerationHistoryPairingMiddleware

@dataclass(frozen=True)
class Request:
    messages: list
    def override(self, **kwargs): return replace(self, **kwargs)

class GenerationHistoryTests(SimpleTestCase):
    def interrupted(self):
        return Request([HumanMessage(content='生成'),AIMessage(content='',tool_calls=[{'name':'execute_skill_script','args':{},'id':'call1'},{'name':'execute_skill_script','args':{},'id':'call2'}]),ToolMessage(content='first result',tool_call_id='call1'),HumanMessage(content='继续')])
    def test_missing_result_is_inserted_before_new_user_message(self):
        result=GenerationHistoryPairingMiddleware().wrap_model_call(self.interrupted(),lambda r:r)
        self.assertEqual(result.messages[-2].tool_call_id,'call2')
        self.assertEqual(result.messages[-2].status,'error')
        self.assertIn('no result was verified',result.messages[-2].content)
        self.assertEqual(result.messages[-1].content,'继续')
    async def test_async_model_request_uses_same_pairing_and_is_idempotent(self):
        async def handler(request):return request
        guard=GenerationHistoryPairingMiddleware()
        result=await guard.awrap_model_call(self.interrupted(),handler)
        again=await guard.awrap_model_call(result,handler)
        self.assertIs(again,result)
    def test_orphan_result_removed_without_inventing_call(self):
        result=GenerationHistoryPairingMiddleware().wrap_model_call(Request([ToolMessage(content='orphan',tool_call_id='orphan'),HumanMessage(content='继续')]),lambda r:r)
        self.assertEqual(len(result.messages),1)
        self.assertEqual(result.messages[0].content,'继续')

    def test_normal_tool_result_is_preserved(self):
        request = Request(self.interrupted().messages[:3])
        request.messages.append(ToolMessage(content='second result', tool_call_id='call2'))
        result = GenerationHistoryPairingMiddleware().wrap_model_call(request, lambda r: r)
        self.assertIs(result, request)

    def test_regular_chat_also_has_final_pairing_guard(self):
        from .api_generation_runtime import generation_middleware
        middleware = generation_middleware([], '')
        self.assertIsInstance(middleware[-1], GenerationHistoryPairingMiddleware)

    def test_legacy_chat_factory_also_repairs_interrupted_history(self):
        from .middleware_config import get_standard_middleware
        middleware = get_standard_middleware(enable_summarization=False, enable_hitl=False)
        self.assertIsInstance(middleware[-1], GenerationHistoryPairingMiddleware)
        result = middleware[-1].wrap_model_call(self.interrupted(), lambda r: r)
        self.assertEqual(result.messages[-2].tool_call_id, 'call2')

    def test_invalid_argument_call_is_also_answered_in_wire_payload(self):
        from langchain_openai.chat_models.base import _convert_message_to_dict
        message = AIMessage(content='', invalid_tool_calls=[{'name': 'execute_skill_script', 'args': '{broken', 'id': 'bad-call', 'error': 'invalid JSON'}])
        result = GenerationHistoryPairingMiddleware().wrap_model_call(Request([message, HumanMessage(content='继续')]), lambda r: r)
        wire = [_convert_message_to_dict(m) for m in result.messages]
        self.assertEqual(wire[0]['tool_calls'][0]['id'], wire[1]['tool_call_id'])
        self.assertEqual(result.messages[1].status, 'error')
