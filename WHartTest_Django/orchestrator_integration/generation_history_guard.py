"""生成任务的最终模型请求保持工具调用与结果成对。"""
import logging
from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)


class GenerationHistoryPairingMiddleware(AgentMiddleware):
    def _prepare(self, request):
        # 复用历史清理算法，在所有上下文裁剪中间件之后再次核对实际模型输入。
        from .agent_loop_view import _build_sanitized_messages
        clean, fixes = _build_sanitized_messages(request.messages)
        if not fixes:
            return request
        logger.info('Generation history pairing repaired %d interrupted/invalid messages', fixes)
        return request.override(messages=clean)

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))
