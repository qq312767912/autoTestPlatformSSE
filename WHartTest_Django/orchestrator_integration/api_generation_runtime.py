"""接口生成会话复用现有服务端 Skill 任务绑定，恢复时不重新选版本。"""
from django.core.exceptions import ValidationError
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from .generation_guard import GenerationInterfaceCaseGuardMiddleware
from .interface_case_instructions import INTERFACE_CASE_LIVE_VERIFICATION_INSTRUCTION

GENERATION_MODES = frozenset({'interface_case', 'scenario'})


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
    if mode not in GENERATION_MODES:
        return prompt
    return (prompt or '') + '\n\n' + INTERFACE_CASE_LIVE_VERIFICATION_INSTRUCTION


def generation_middleware(middleware, mode):
    result = list(middleware or [])
    if mode in GENERATION_MODES:
        result.append(GenerationInterfaceCaseGuardMiddleware(mode))
    return result
