import json
import shlex
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from projects.models import Project, ProjectMember
from skills.versions import SkillVersionService
from knowledge_evolution.skill_agent_tasks import SkillAgentTaskViewSet, resolve_agent_task_version
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from orchestrator_integration.api_generation_runtime import resolve_generation_mode, append_generation_instructions, generation_middleware
from orchestrator_integration.generation_guard import GenerationInterfaceCaseGuardMiddleware


@dataclass(frozen=True)
class ToolRequest:
    tool_call: dict
    def override(self, **kwargs):
        return replace(self, **kwargs)


class InterfaceGuardTests(SimpleTestCase):
    def request(self, action, payload):
        command = shlex.join(['python', 'tools.py', '--action', action, '--payload', json.dumps(payload, ensure_ascii=False)])
        return ToolRequest({'id': 'call-1', 'name': 'execute_skill_script', 'args': {'command': command}})

    def test_scene_save_is_blocked_in_interface_mode(self):
        handler = Mock()
        message = GenerationInterfaceCaseGuardMiddleware().wrap_tool_call(self.request('create_testcase', {}), handler)
        self.assertEqual(message.status, 'error')
        handler.assert_not_called()

    def test_group_removal_preserves_json_and_shell_quoting(self):
        captured = []
        handler = lambda request: captured.append(request)
        original = self.request('create_interface_case', {'name': "中文 带空格 ' 引号", 'group': 3, 'interface_id': 9})
        GenerationInterfaceCaseGuardMiddleware().wrap_tool_call(original, handler)
        tokens = shlex.split(captured[0].tool_call['args']['command'])
        data = json.loads(tokens[tokens.index('--payload') + 1])
        self.assertEqual(data, {'name': "中文 带空格 ' 引号", 'interface_id': 9})

    async def test_async_guard_uses_same_payload_contract(self):
        async def handler(request):
            tokens = shlex.split(request.tool_call['args']['command'])
            return json.loads(tokens[tokens.index('--payload') + 1])
        result = await GenerationInterfaceCaseGuardMiddleware().awrap_tool_call(self.request('update_interface_case', {'name': 'a b', 'group': 4}), handler)
        self.assertEqual(result, {'name': 'a b'})

    def test_equals_action_and_direct_tools_cannot_bypass_guard(self):
        guard = GenerationInterfaceCaseGuardMiddleware()
        handler = Mock()
        command = ToolRequest({'id': 'eq', 'name': 'execute_skill_script', 'args': {'command': 'python tools.py --action=create_testcase'}})
        self.assertEqual(guard.wrap_tool_call(command, handler).status, 'error')
        direct = ToolRequest({'id': 'direct', 'name': 'create_testcase', 'args': {}})
        self.assertEqual(guard.wrap_tool_call(direct, handler).status, 'error')
        handler.assert_not_called()

    def test_scenario_mode_blocks_wrong_save_and_keeps_group(self):
        guard = GenerationInterfaceCaseGuardMiddleware('scenario')
        handler = Mock(return_value='saved')
        self.assertEqual(guard.wrap_tool_call(self.request('create_interface_case', {}), handler).status, 'error')
        handler.assert_not_called()
        self.assertEqual(guard.wrap_tool_call(self.request('create_testcase', {'group': 4}), handler), 'saved')

    def test_uninspectable_payload_does_not_bypass_parameter_guard(self):
        request = ToolRequest({'id': 'file', 'name': 'execute_skill_script', 'args': {'command': 'python tools.py --action create_interface_case --payload @payload.json'}})
        handler = Mock()
        self.assertEqual(GenerationInterfaceCaseGuardMiddleware().wrap_tool_call(request, handler).status, 'error')
        handler.assert_not_called()

    def test_project_scope_cannot_be_changed_by_duplicate_flags(self):
        guard = GenerationInterfaceCaseGuardMiddleware(project_id=22)
        handler = Mock()
        for command in ['python tools.py --project_id 1 --action list_interfaces', 'python tools.py --project_id=22 --project_id=1 --action=create_interface_case']:
            request = ToolRequest({'id': 'project', 'name': 'execute_skill_script', 'args': {'command': command}})
            self.assertEqual(guard.wrap_tool_call(request, handler).status, 'error')
        handler.assert_not_called()

    def test_verification_policy_applies_to_both_generation_modes(self):
        self.assertEqual(append_generation_instructions('base', ''), 'base')
        for mode in ('interface_case', 'scenario'):
            self.assertIn('未试跑不得宣称', append_generation_instructions('base', mode))
        self.assertEqual(len(generation_middleware([], 'interface_case')), 1)
        self.assertEqual(len(generation_middleware([], 'scenario')), 1)


class ApiGenerationBindingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.temp.name + '/media')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user(username='generation-user')
        self.other = User.objects.create_user(username='other-generation-user')
        self.project = Project.objects.create(name='Generation', creator=self.user)
        ProjectMember.objects.update_or_create(project=self.project, user=self.user, defaults={'role': 'owner'})
        root = Path(self.temp.name) / 'source'
        root.mkdir()
        (root / 'SKILL.md').write_text('---\nname: selected-generation-skill\ndescription: 接口生成\nversion: 1.0.0\nstage: testcase_generation\n---\n唯一版本指令\n')
        self.skill, self.version = SkillVersionService.create_candidate_from_dir(source_dir=root, project=self.project, actor=self.user, source_type='project_copy')

    def create_binding(self, mode='interface_case'):
        request = APIRequestFactory().post('/', {'project': self.project.pk, 'source_version_id': str(self.version.pk), 'generation_mode': mode}, format='json')
        force_authenticate(request, user=self.user)
        return SkillAgentTaskViewSet.as_view({'post': 'create'})(request)

    def test_binding_preserves_mode_and_version_on_resume(self):
        response = self.create_binding()
        self.assertEqual(response.status_code, 201, response.data)
        session_id = response.data['session_id']
        self.assertEqual(resolve_generation_mode(session_id=session_id, actor=self.user, project_id=self.project.pk), 'interface_case')
        self.assertEqual(resolve_agent_task_version(session_id=session_id, actor=self.user, project_id=self.project.pk), str(self.version.pk))
        self.assertEqual(SkillAgentTaskBinding.objects.get(session__session_id=session_id).generation_mode, 'interface_case')

    def test_cannot_change_mode_or_owner(self):
        session_id = self.create_binding().data['session_id']
        with self.assertRaises(ValidationError):
            resolve_generation_mode(session_id=session_id, actor=self.user, project_id=self.project.pk, message='<!-- wharttest:generation_mode=scenario -->')
        with self.assertRaises(ValidationError):
            resolve_generation_mode(session_id=session_id, actor=self.other, project_id=self.project.pk)
        with self.assertRaises(ValidationError):
            resolve_generation_mode(session_id=session_id, actor=self.user, project_id=self.project.pk + 1)

    def test_unbound_generation_and_unknown_mode_are_rejected(self):
        with self.assertRaises(ValidationError):
            resolve_generation_mode(session_id='', actor=self.user, project_id=self.project.pk, message='<!-- wharttest:generation_mode=interface_case -->')
        self.assertEqual(self.create_binding('unknown-mode').status_code, 400)

    def test_general_chat_does_not_acquire_generation_policy(self):
        self.assertEqual(resolve_generation_mode(session_id='', actor=self.user, project_id=self.project.pk, message='普通聊天'), '')

    def test_locked_tool_cannot_read_another_skill(self):
        from orchestrator_integration.builtin_tools.skill_tools import get_skill_tools
        tools = get_skill_tools(self.user.pk, project_id=self.project.pk, selected_skill_version_id=str(self.version.pk))
        read = next(tool for tool in tools if tool.name == 'read_skill_content')
        self.assertIn('唯一版本指令', read.invoke({'skill_name': self.skill.name}))
        self.assertIn('锁定其他 Skill', read.invoke({'skill_name': 'another-skill'}))
