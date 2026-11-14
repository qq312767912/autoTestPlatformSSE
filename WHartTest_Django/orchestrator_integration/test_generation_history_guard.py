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
