"""接口生成会话复用现有服务端 Skill 任务绑定，恢复时不重新选版本。"""
from django.core.exceptions import ValidationError
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from .generation_guard import GenerationInterfaceCaseGuardMiddleware
from .interface_case_instructions import INTERFACE_CASE_LIVE_VERIFICATION_INSTRUCTION

GENERATION_MODES = frozenset({'interface_case', 'scenario', 'ui_case', 'ui_healing'})


def resolve_generation_mode(*, session_id, actor, project_id, message=''):
    binding = SkillAgentTaskBinding.objects.select_related('session').filter(session__session_id=session_id).first() if session_id else None
    requested = ''
    for mode in GENERATION_MODES:
        if str(message or '').lstrip().startswith(f'<!-- wharttest:generation_mode={mode} -->'):
            requested = mode
            break
    if binding and binding.generation_mode:
        if binding.session.user_id != actor.pk or binding.session.project_id != int(project_id):
            raise ValidationError('生成任务不属于当前用户和项目')
        if requested and requested != binding.generation_mode:
            raise ValidationError('生成任务模式已锁定，不能在会话内切换')
        return binding.generation_mode
    if requested:
        raise ValidationError('请从生成入口选择并锁定 Skill 精确版本')
    return ''


def append_generation_instructions(prompt, mode):
    if mode == "ui_healing":
        return (prompt or "") + "\n\n当前任务是已有 UI 用例批量自愈。MUST 读取 get_ui_healing_context；仅修复当前用例，NEVER 删除或弱化业务断言，NEVER 新建替代用例。NEVER 执行脚本或宣布成功，平台统一重跑验证。浏览器操作 MUST 使用 ui_generation_browser。"
    if mode == "ui_case":
        from .ui_generation_runtime import UI_GENERATION_INSTRUCTION
        return (prompt or "") + "\n\n" + UI_GENERATION_INSTRUCTION
    if mode not in GENERATION_MODES:
        return prompt
    return (prompt or '') + '\n\n' + INTERFACE_CASE_LIVE_VERIFICATION_INSTRUCTION


def generation_middleware(middleware, mode, project_id=None, session_id=None):
    result = list(middleware or [])
    if mode == "ui_case":
        from .generation_guard import GenerationUiCaseGuardMiddleware
        result.append(GenerationUiCaseGuardMiddleware(mode, project_id, session_id))
    if mode == "ui_healing":
        from .generation_guard import UiHealingGuardMiddleware
        result.append(UiHealingGuardMiddleware(mode, project_id, session_id))
    if mode in GENERATION_MODES - {"ui_case", "ui_healing"}:
        result.append(GenerationInterfaceCaseGuardMiddleware(mode, project_id))
    # 普通聊天与恢复会话也可能包含中断的工具调用；在最终模型输入上统一配对。
    from .generation_history_guard import GenerationHistoryPairingMiddleware
    if not any(isinstance(item, GenerationHistoryPairingMiddleware) for item in result):
        result.append(GenerationHistoryPairingMiddleware())
    return result
