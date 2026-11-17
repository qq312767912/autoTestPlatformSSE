"""自愈只暴露当前用例上下文和任务隔离的浏览器。"""
import json
from asgiref.sync import sync_to_async
from langchain_core.tools import tool
from orchestrator_integration.ui_generation_runtime import get_binding


def healing_tools(session_id, user_id, project_id):
    @tool
    async def get_ui_healing_context() -> str:
        """读取当前自愈用例、原始断言、首跑失败记录和实际环境。修复前调用。"""
        def read():
            binding = get_binding(session_id, user_id, project_id)
            case_id = binding.generation_run.get('active_source_id')
            snapshot = next(x for x in binding.generation_context['ui_cases'] if x['id'] == case_id)
            item = next(x for x in binding.generation_run['items'] if x['id'] == case_id)
            from .models import UiExecutionRecord
            record = UiExecutionRecord.objects.get(pk=item['initial_record_id'], test_case_id=case_id)
            return {**snapshot, 'env_config_id': binding.generation_context['env_config_id'],
                'base_url': binding.generation_context['base_url'], 'failure': {'record_id': record.pk,
                'error': (record.error_message or '')[:4000], 'step_results': [{key: value for key, value in step.items() if key in {'status', 'message', 'description', 'step_id', 'duration'}} for step in (record.step_results or [])][:100]}}
        return json.dumps(await sync_to_async(read)(), ensure_ascii=False)

    @tool
    async def ui_generation_browser(command: str, arguments: list[str]) -> str:
        """操作任务隔离的真实浏览器：open [URL]、snapshot []、click [@e1]、fill [@e2,文本]、get [attr,@e1,id]、close []。禁止输出凭据。"""
        from orchestrator_integration.ui_generation_browser import run_ui_browser
        binding = await sync_to_async(get_binding)(session_id, user_id, project_id)
        try:
            return await sync_to_async(run_ui_browser)(binding, command, arguments)
        except Exception:
            return json.dumps({'error': '浏览器操作失败，请检查环境、登录态和参数'}, ensure_ascii=False)
    return [get_ui_healing_context, ui_generation_browser]
