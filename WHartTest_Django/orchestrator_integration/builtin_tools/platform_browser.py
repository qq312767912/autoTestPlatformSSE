"""平台浏览器基础能力，不通过任意 Skill 脚本维持后台进程。"""
from types import SimpleNamespace
from asgiref.sync import sync_to_async
from langchain_core.tools import tool


def _binding(user_id,project_id,session_id):
    from langgraph_integration.models import ChatSession
    session=ChatSession.objects.select_related('auth_state__env_config').get(
        session_id=session_id,user_id=user_id,project_id=project_id)
    context={'auth_state_id':session.auth_state_id}
    if session.auth_state_id:
        if session.auth_state.env_config.project_id!=int(project_id):
            raise ValueError('登录态不属于当前项目')
        context['env_config_id']=session.auth_state.env_config_id
    return SimpleNamespace(session=session,generation_context=context)


def get_platform_browser_tools(user_id,project_id,session_id):
    if not session_id:return []
    @tool
    async def platform_browser(command:str,arguments:list[str])->str:
        """按当前用户、项目和会话隔离浏览器。先open [URL]，snapshot []读取真实元素ref；再click [@e1]、fill [@e2,文本]、get [attr,@e1,id]、eval [JS]；结束close []。登录态由平台注入，禁止读取或输出凭据。"""
        from orchestrator_integration.ui_generation_browser import run_ui_browser
        try:
            binding=await sync_to_async(_binding)(user_id,project_id,session_id)
            return await sync_to_async(run_ui_browser)(binding,command,arguments)
        except Exception as exc:
            return '浏览器操作失败：'+str(exc)
    return [platform_browser]
