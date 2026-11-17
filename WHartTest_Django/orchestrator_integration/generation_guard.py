"""生成会话工具守护：区分单接口/场景用例，并保持 payload 的 Shell 引用。"""
from __future__ import annotations
import json
import shlex
from typing import Any
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

INTERFACE_ALTERNATIVES = {
    'create_testcase': 'create_interface_case', 'update_testcase': 'update_interface_case',
    'run_testcase': 'run_interface_case', 'batch_run_testcases': 'run_interface_case',
    'copy_testcase': 'copy_interface_case', 'delete_testcase': 'delete_interface_case',
    'delete_testcase_step': 'update_interface_case', 'update_testcase_step': 'update_interface_case',
    'reorder_testcase_steps': 'update_interface_case',
}
SCENARIO_ALTERNATIVES = {
    'create_interface_case': 'create_testcase', 'update_interface_case': 'update_testcase',
    'run_interface_case': 'run_testcase', 'copy_interface_case': 'copy_testcase',
    'delete_interface_case': 'delete_testcase',
}


def _error(request, message):
    call = request.tool_call or {}
    return ToolMessage(content=message, tool_call_id=call.get('id') or '', name=call.get('name') or 'tool', status='error')


def _flag(tokens, name):
    for index, token in enumerate(tokens):
        if token == name and index + 1 < len(tokens):
            return tokens[index + 1], index + 1, False
        if token.startswith(name + '='):
            return token.split('=', 1)[1], index, True
    return '', None, False


class GenerationInterfaceCaseGuardMiddleware(AgentMiddleware):
    def __init__(self, mode='interface_case', project_id=None, session_id=None):
        super().__init__()
        self.session_id = session_id
        self.mode = mode
        self.project_id = str(project_id) if project_id is not None else None
        self._drift_counts = {'tool': 0, 'param': 0}

    @property
    def drift_counts(self):
        return dict(self._drift_counts)

    def _prepare(self, request: Any):
        call = request.tool_call or {}
        args = dict(call.get('args') or {})
        command = str(args.get('command') or '')
        tokens = []
        if command:
            try:
                tokens = shlex.split(command)
            except ValueError:
                return request, _error(request, '命令引用不完整，请修正后重试。')
            action, _, _ = _flag(tokens, '--action')
        else:
            action = call.get('name') or ''
        if self.project_id is not None:
            projects = []
            for index, token in enumerate(tokens):
                if token == '--project_id':
                    projects.append(tokens[index + 1] if index + 1 < len(tokens) else '')
                elif token.startswith('--project_id='):
                    projects.append(token.split('=', 1)[1])
            if not command and args.get('project_id') is not None:
                projects.append(str(args['project_id']))
            if any(str(project) != self.project_id for project in projects):
                return request, _error(request, f'本次生成任务已绑定项目 {self.project_id}，禁止切换到其他项目。')
        forbidden = SCENARIO_ALTERNATIVES if self.mode == 'scenario' else INTERFACE_ALTERNATIVES
        if action in forbidden:
            self._drift_counts['tool'] += 1
            kind = '场景用例' if self.mode == 'scenario' else '单接口用例'
            return request, _error(request, f'当前任务是{kind}生成，禁止调用 {action}，请改用 {forbidden[action]}。')
        if self.mode == 'scenario' or action not in {'create_interface_case', 'update_interface_case'}:
            return request, None

        if command:
            raw, index, equals = _flag(tokens, '--payload')
            if index is None:
                return request, None
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return request, _error(request, '单接口用例保存请使用 --payload 内联 JSON 对象，以校验归属参数。')
        else:
            payload = args.get('payload', args)
        if not isinstance(payload, dict):
            return request, _error(request, '用例保存 payload 必须是 JSON 对象。')
        if 'group' not in payload:
            return request, None
        self._drift_counts['param'] += 1
        payload = {key: value for key, value in payload.items() if key != 'group'}
        if command:
            value = json.dumps(payload, ensure_ascii=False)
            tokens[index] = '--payload=' + value if equals else value
            args['command'] = shlex.join(tokens)
        elif 'payload' in args:
            args['payload'] = payload
        else:
            args = payload
        try:
            return request.override(tool_call={**call, 'args': args}), None
        except (AttributeError, TypeError):
            return request, _error(request, '请移除单接口用例的 group 参数后重试。')

    def wrap_tool_call(self, request, handler):
        updated, error = self._prepare(request)
        return error if error is not None else handler(updated)

    async def awrap_tool_call(self, request, handler):
        from asgiref.sync import sync_to_async
        updated, error = await sync_to_async(self._prepare)(request)
        return error if error is not None else await handler(updated)


class GenerationUiCaseGuardMiddleware(GenerationInterfaceCaseGuardMiddleware):
    """UI 生成保持项目范围，并阻止通过功能用例编辑工具跳过试跑绑定。"""
    def _prepare(self, request):
        call = request.tool_call or {}
        args = call.get('args') or {}
        if args.get('commands'):
            return request, _error(request, 'UI 生成请逐条执行命令，以便校验任务范围和试跑结果。')
        command = str(args.get('command') or '')
        try:
            tokens = shlex.split(command) if command else []
        except ValueError:
            return request, _error(request, '命令引用不完整')
        action = _flag(tokens, '--action')[0] if command else call.get('name', '')
        # Compatibility for immutable older builtin packages whose CLI help omitted action names.
        aliases = {'list_modules': 'get_ui_modules', 'list_testcases': 'get_testcases', 'get_testcase_detail': 'get_testcase'}
        if command and action in aliases:
            _, index, equals = _flag(tokens, '--action')
            action = aliases[action]
            tokens[index] = '--action=' + action if equals else action
            args = dict(args)
            args['command'] = shlex.join(tokens)
            request = request.override(tool_call={**call, 'args': args})
        if action in {'edit_testcase', 'create_interface_case', 'update_interface_case', 'run_interface_case', 'run_testcase'}:
            return request, _error(request, '当前是 UI 生成任务；功能用例绑定必须通过 bind_generated_ui_case 验证成功试跑记录。')
        if action == 'execute_testcase' and self.session_id:
            try:
                from .ui_generation_runtime import lock_trial_assertions
                ui_id = _flag(tokens, '--testcase_id')[0] if command else args.get('testcase_id')
                env_id = _flag(tokens, '--config_id')[0] if command else args.get('config_id')
                lock_trial_assertions(self.session_id, self.project_id, ui_id, env_id)
            except Exception:
                return request, _error(request, '试跑范围或断言锁定校验失败：请核对环境、用例归属，并保留首次试跑的业务断言。')
        if action == 'execute_testcase':
            if command:
                actuator, _, _ = _flag(tokens, '--actuator_id')
                if actuator and actuator != 'recorder-browser':
                    return request, _error(request, '探索转换试跑必须使用平台录制器浏览器 recorder-browser。')
                if not actuator:
                    args = dict(args)
                    args['command'] = shlex.join(tokens + ['--actuator_id', 'recorder-browser'])
                    request = request.override(tool_call={**call, 'args': args})
            elif args.get('actuator_id') not in (None, 'recorder-browser'):
                return request, _error(request, '探索转换试跑执行器不匹配。')
        # Reuse project validation without the API save/action mapping.
        for index, token in enumerate(tokens):
            value = tokens[index + 1] if token == '--project_id' and index + 1 < len(tokens) else token.split('=', 1)[1] if token.startswith('--project_id=') else None
            if value is not None and str(value) != self.project_id:
                return request, _error(request, 'UI 生成任务禁止切换项目')
        if args.get('project_id') is not None and str(args['project_id']) != self.project_id:
            return request, _error(request, 'UI 生成任务禁止切换项目')
        return request, None


class UiHealingGuardMiddleware(GenerationUiCaseGuardMiddleware):
    def _prepare(self, request):
        call = request.tool_call or {}
        args = call.get('args') or {}
        try:
            tokens = shlex.split(str(args.get('command') or ''))
        except ValueError:
            return request, _error(request, '命令引用不完整')
        action = _flag(tokens, '--action')[0] if tokens else call.get('name', '')
        if action.startswith(('delete_', 'create_testcase', 'copy_testcase')) or action in {'execute_testcase', 'batch_execute_testcases', 'bind_generated_ui_case', 'edit_testcase', 'set_case_steps', 'update_testcase', 'update_env_config', 'create_env_config'}:
            return request, _error(request, '自愈仅维护已有用例，禁止删除、新建替代或自行执行；平台统一重跑验证。')
        if action in {'update_element', 'create_element', 'set_step_details', 'update_page_step'}:
            try:
                self._validate_edit(action, tokens, args)
            except Exception:
                return request, _error(request, '自愈修改超出当前用例范围，或变更了原业务断言；请保留原断言及步骤引用。')
        return super()._prepare(request)

    def _validate_edit(self, action, tokens, args):
        from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
        from ui_automation.models import UiPageSteps, UiPageStepsDetailed, UiElement
        binding = SkillAgentTaskBinding.objects.get(session__session_id=self.session_id, generation_mode='ui_healing')
        active = binding.generation_run['active_source_id']
        groups = UiPageSteps.objects.filter(case_usages__test_case_id=active)
        def value(name):
            return _flag(tokens, '--' + name)[0] if tokens else args.get(name)
        if action == 'update_element':
            if not UiElement.objects.filter(pk=value('element_id'), page_id__in=groups.values('page_id')).exists():
                raise ValueError('element scope')
        elif action == 'create_element':
            if not groups.filter(page_id=value('page_id')).exists():
                raise ValueError('page scope')
        else:
            step_id = value('step_id')
            if not groups.filter(pk=step_id).exists():
                raise ValueError('step scope')
            if action == 'set_step_details':
                steps = json.loads(value('steps')) if tokens else args.get('steps')
                if not isinstance(steps, list): raise ValueError('steps')
                original = list(UiPageStepsDetailed.objects.filter(page_step_id=step_id, step_type=1).order_by('step_sort', 'pk').values('element_id', 'ope_key', 'ope_value'))
                proposed = [{'element_id': x.get('element', x.get('element_id')), 'ope_key': x.get('ope_key'), 'ope_value': x.get('ope_value')} for x in steps if int(x.get('step_type', 0)) == 1]
                if original != proposed: raise ValueError('assertion change')
