from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import Mock
from .test_api_generation import ApiGenerationBindingTests, ToolRequest
from .ui_generation_runtime import validate_ui_context, bind_generated_case, ui_generation_tools
from .api_generation_runtime import resolve_generation_mode, append_generation_instructions, generation_middleware
from knowledge_evolution.skill_agent_tasks import SkillAgentTaskViewSet
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from ui_automation.models import UiModule, UiEnvironmentConfig, UiAuthState, UiTestCase, UiPage, UiPageSteps, UiCaseStepsDetailed, UiExecutionRecord, UiPageStepsDetailed
from testcases.models import TestCase as FunctionalCase, TestCaseStep, TestCaseModule


class UiGenerationTests(ApiGenerationBindingTests):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()
        self.module = UiModule.objects.create(project=self.project, name='UI', creator=self.user)
        self.env = UiEnvironmentConfig.objects.create(project=self.project, name='test', base_url='http://localhost', creator=self.user)
        fm=TestCaseModule.objects.create(project=self.project,name='功能',creator=self.user)
        self.case = FunctionalCase.objects.create(project=self.project, module=fm, name='真实功能步骤', creator=self.user, precondition='')
        TestCaseStep.objects.create(test_case=self.case, step_number=1, description='提交申请', expected_result='显示申请编号')
        self.raw = {'functional_case_ids':[self.case.pk], 'module_id':self.module.pk, 'env_config_id':self.env.pk, 'auth_state_id':None}

    def binding(self):
        request = APIRequestFactory().post('/', {'project':self.project.pk, 'source_version_id':str(self.version.pk), 'generation_mode':'ui_case', 'generation_context':self.raw}, format='json')
        force_authenticate(request, self.user)
        response = SkillAgentTaskViewSet.as_view({'post':'create'})(request)
        self.assertEqual(response.status_code, 201, response.data)
        return SkillAgentTaskBinding.objects.get(session__session_id=response.data['session_id'])

    def test_ui_context_is_server_snapshot_and_resume_keeps_mode(self):
        binding = self.binding()
        self.assertEqual(binding.generation_context['functional_cases'][0]['steps'][0]['description'], '提交申请')
        self.assertEqual(resolve_generation_mode(session_id=binding.session.session_id, actor=self.user, project_id=self.project.pk), 'ui_case')
        with self.assertRaises(ValidationError):
            resolve_generation_mode(session_id=binding.session.session_id, actor=self.user, project_id=self.project.pk, message='<!-- wharttest:generation_mode=scenario -->')
        self.assertEqual([t.name for t in ui_generation_tools('ui_case', binding.session.session_id, self.user.pk, self.project.pk)], ['get_ui_generation_context','bind_generated_ui_case','ui_generation_browser'])
        self.assertEqual(ui_generation_tools('', '', 1, 1), [])

    def test_cross_project_module_and_auth_and_missing_permission_rejected(self):
        self.raw['module_id'] = 9999999
        with self.assertRaises(ValidationError): validate_ui_context(self.project, self.user, self.raw)
        self.raw['module_id'] = self.module.pk
        other_env = UiEnvironmentConfig.objects.create(project=self.project, name='other', base_url='http://other')
        auth = UiAuthState.objects.create(env_config=other_env, name='wrong environment', state_json={'cookies':[],'origins':[]})
        self.raw['auth_state_id'] = auth.pk
        with self.assertRaises(ValidationError): validate_ui_context(self.project, self.user, self.raw)
        with self.assertRaises(ValidationError): validate_ui_context(self.project, self.other, self.raw)

    def prepare_ui(self):
        binding = self.binding()
        ui = UiTestCase.objects.create(project=self.project, module=self.module, name='自动化', creator=self.user)
        page = UiPage.objects.create(project=self.project, module=self.module, name='page', creator=self.user)
        step = UiPageSteps.objects.create(project=self.project, module=self.module, page=page, name='step', creator=self.user)
        UiPageStepsDetailed.objects.create(page_step=step,step_type=1,ope_key='assert_text',ope_value={'expected':'成功'})
        UiCaseStepsDetailed.objects.create(test_case=ui, page_step=step)
        return binding, ui

    def bind(self, binding, ui):
        return bind_generated_case(binding.session.session_id, self.user.pk, self.project.pk, self.case.pk, ui.pk)

    def test_binding_requires_latest_success_and_matching_environment(self):
        binding, ui = self.prepare_ui()
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record = UiExecutionRecord.objects.create(test_case=ui, executor=self.user, status=3, start_time=timezone.now(), environment={'env_config_id':self.env.pk})
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record.status=2; record.environment={'env_config_id':9999}; record.save()
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record.environment={'env_config_id':self.env.pk}; record.save()
        self.assertTrue(self.bind(binding, ui)['bound'])
        self.case.refresh_from_db(); self.assertEqual(self.case.ui_test_case_id, ui.pk)
        self.assertTrue(self.bind(binding, ui)['bound'])

    def test_changed_functional_steps_and_stale_execution_rejected(self):
        binding, ui = self.prepare_ui()
        record=UiExecutionRecord.objects.create(test_case=ui, status=2, start_time=binding.created_at, environment={'env_config_id':self.env.pk})
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record.start_time=timezone.now();record.save()
        self.case.steps.update(description='改过的步骤')
        with self.assertRaises(ValidationError): self.bind(binding, ui)

    def test_ui_guard_allows_ui_save_and_blocks_binding_bypass(self):
        guard=generation_middleware([], 'ui_case', self.project.pk)[0]
        handler=Mock(return_value='ok')
        for action in ['edit_testcase','run_testcase','create_interface_case']:
            request=ToolRequest({'id':'x','name':'execute_skill_script','args':{'command':f'python tools.py --action {action}'}})
            self.assertEqual(guard.wrap_tool_call(request,handler).status,'error')
        handler.assert_not_called()
        request=ToolRequest({'id':'x','name':'execute_skill_script','args':{'command':'python tools.py --action create_testcase'}})
        self.assertEqual(guard.wrap_tool_call(request,handler),'ok')
        self.assertIn('bind_generated_ui_case',append_generation_instructions('', 'ui_case'))
