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
    def __init__(self, mode='interface_case', project_id=None):
        super().__init__()
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
        updated, error = self._prepare(request)
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
        if action in {'edit_testcase', 'create_interface_case', 'update_interface_case', 'run_interface_case', 'run_testcase'}:
            return request, _error(request, '当前是 UI 生成任务；功能用例绑定必须通过 bind_generated_ui_case 验证成功试跑记录。')
        # Reuse project validation without the API save/action mapping.
        for index, token in enumerate(tokens):
            value = tokens[index + 1] if token == '--project_id' and index + 1 < len(tokens) else token.split('=', 1)[1] if token.startswith('--project_id=') else None
            if value is not None and str(value) != self.project_id:
                return request, _error(request, 'UI 生成任务禁止切换项目')
        if args.get('project_id') is not None and str(args['project_id']) != self.project_id:
            return request, _error(request, 'UI 生成任务禁止切换项目')
        return request, None
