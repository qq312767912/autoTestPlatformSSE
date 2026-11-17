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
        from unittest.mock import patch
        resolver = patch('knowledge_evolution.skill_agent_tasks.builtin_ui_version', return_value=self.version)
        resolver.start()
        self.addCleanup(resolver.stop)
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
        record = UiExecutionRecord.objects.create(executor=self.user, test_case=ui, status=3, start_time=timezone.now(), environment={'env_config_id':self.env.pk})
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record.status=2; record.environment={'env_config_id':9999}; record.save()
        with self.assertRaises(ValidationError): self.bind(binding, ui)
        record.environment={'env_config_id':self.env.pk}; record.save()
        self.assertTrue(self.bind(binding, ui)['bound'])
        self.case.refresh_from_db(); self.assertEqual(self.case.ui_test_case_id, ui.pk)
        self.assertTrue(self.bind(binding, ui)['bound'])

    def test_changed_functional_steps_and_stale_execution_rejected(self):
        binding, ui = self.prepare_ui()
        record=UiExecutionRecord.objects.create(executor=self.user, test_case=ui, status=2, start_time=binding.created_at, environment={'env_config_id':self.env.pk})
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

    def test_existing_binding_is_preserved_and_still_requires_fresh_evidence(self):
        binding, ui = self.prepare_ui()
        self.case.ui_test_case = ui
        self.case.save()
        current = self.binding()
        self.assertEqual(current.generation_context['functional_cases'][0]['ui_test_case_id'], ui.pk)
        with self.assertRaises(ValidationError): self.bind(current, ui)
        UiExecutionRecord.objects.create(test_case=ui, executor=self.user, status=2, start_time=timezone.now(), environment={'env_config_id':self.env.pk})
        self.assertTrue(self.bind(current, ui)['bound'])

    def test_existing_binding_cannot_silently_switch_target(self):
        binding, ui = self.prepare_ui()
        self.case.ui_test_case = ui
        self.case.save()
        current = self.binding()
        other = UiTestCase.objects.create(project=self.project,module=self.module,name='wrong',creator=self.user)
        with self.assertRaises(ValidationError): self.bind(current, other)

    def test_batch_start_is_idempotent_and_queues_only_after_commit(self):
        from unittest.mock import patch
        binding = self.binding()
        def start():
            request=APIRequestFactory().post('/',{'project':self.project.pk,'session_id':binding.session.session_id},format='json')
            force_authenticate(request,self.user)
            return SkillAgentTaskViewSet.as_view({'post':'start_conversion'})(request)
        with patch('knowledge_evolution.ui_generation_jobs.run_ui_conversion.delay') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(start().status_code,202)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(start().status_code,202)
            enqueue.assert_called_once_with(binding.pk)

    def test_batch_worker_does_not_accept_model_claim_as_verification(self):
        from unittest.mock import patch, AsyncMock
        from knowledge_evolution.ui_generation_jobs import run_ui_conversion
        binding=self.binding()
        binding.generation_run={'status':'queued'}
        binding.save(update_fields=['generation_run'])
        with patch('knowledge_evolution.ui_generation_jobs.consume',new_callable=AsyncMock):
            run_ui_conversion(binding.pk)
        binding.refresh_from_db()
        self.assertEqual(binding.generation_run['status'],'partial_failure')
        self.assertEqual(binding.generation_run['items'][0]['status'],'needs_attention')

    def test_trial_execution_uses_locked_platform_recorder(self):
        guard=generation_middleware([], 'ui_case', self.project.pk)[0]
        request=ToolRequest({'id':'x','name':'execute_skill_script','args':{'command':f'python tools.py --action execute_testcase --testcase_id 1 --config_id {self.env.pk}'}})
        result=guard.wrap_tool_call(request,lambda r:r)
        self.assertIn('--actuator_id recorder-browser',result.tool_call['args']['command'])

    def test_trial_assertions_cannot_be_weakened_between_retries(self):
        from .ui_generation_runtime import lock_trial_assertions
        binding, ui=self.prepare_ui()
        lock_trial_assertions(binding.session.session_id,self.project.pk,ui.pk,self.env.pk)
        detail=ui.case_steps.first().page_step.step_details.first()
        detail.ope_value={'expected':''};detail.save()
        with self.assertRaises(ValidationError):
            lock_trial_assertions(binding.session.session_id,self.project.pk,ui.pk,self.env.pk)

    def test_cancelled_batch_does_not_start_exploration(self):
        from unittest.mock import patch, AsyncMock
        from knowledge_evolution.ui_generation_jobs import run_ui_conversion
        binding=self.binding()
        binding.generation_run={'status':'queued','cancel_requested':True}
        binding.save(update_fields=['generation_run'])
        with patch('knowledge_evolution.ui_generation_jobs.consume',new_callable=AsyncMock) as consume:
            run_ui_conversion(binding.pk)
        consume.assert_not_called()
        binding.refresh_from_db()
        self.assertEqual(binding.generation_run['status'],'cancelled')
        self.assertEqual(binding.generation_run['items'][0]['status'],'cancelled')

    def test_dispatch_failure_does_not_leave_batch_queued_forever(self):
        from unittest.mock import patch
        binding=self.binding()
        request=APIRequestFactory().post('/',{'project':self.project.pk,'session_id':binding.session.session_id},format='json')
        force_authenticate(request,self.user)
        with patch('knowledge_evolution.ui_generation_jobs.run_ui_conversion.delay',side_effect=RuntimeError('broker down')):
            with self.captureOnCommitCallbacks(execute=True):
                response=SkillAgentTaskViewSet.as_view({'post':'start_conversion'})(request)
        self.assertEqual(response.status_code,202)
        binding.refresh_from_db()
        self.assertEqual(binding.generation_run['status'],'failed')

    def test_cancellation_during_exploration_marks_active_item_cancelled(self):
        from unittest.mock import patch
        from asgiref.sync import sync_to_async
        from knowledge_evolution.ui_generation_jobs import run_ui_conversion, update_run
        binding=self.binding()
        binding.generation_run={'status':'queued'}
        binding.save(update_fields=['generation_run'])
        async def cancel_in_flight(child, message, parent_id=None):
            await sync_to_async(update_run)(parent_id,cancel_requested=True)
        with patch('knowledge_evolution.ui_generation_jobs.consume',side_effect=cancel_in_flight):
            run_ui_conversion(binding.pk)
        binding.refresh_from_db()
        self.assertEqual(binding.generation_run['status'],'cancelled')
        self.assertEqual(binding.generation_run['items'][0]['status'],'cancelled')

    def test_ui_task_uses_builtin_without_client_skill_selection(self):
        request=APIRequestFactory().post('/',{'project':self.project.pk,'generation_mode':'ui_case','generation_context':self.raw},format='json')
        force_authenticate(request,self.user)
        response=SkillAgentTaskViewSet.as_view({'post':'create'})(request)
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['skill_version_id'],str(self.version.pk))

    def test_old_cli_list_aliases_are_normalized_without_mutating_arguments(self):
        guard=generation_middleware([], 'ui_case', self.project.pk)[0]
        for alias, canonical in [('list_modules','get_ui_modules'),('list_testcases','get_testcases')]:
            request=ToolRequest({'id':'x','name':'execute_skill_script','args':{'command':f'python ui_automation_tools.py --action={alias} --project_id {self.project.pk}'}})
            result=guard.wrap_tool_call(request,lambda r:r)
            self.assertIn('--action='+canonical,result.tool_call['args']['command'])
            self.assertIn('--project_id '+str(self.project.pk),result.tool_call['args']['command'])

    def test_cli_help_lists_real_actions_and_read_aliases_work(self):
        import runpy, sys, io
        from pathlib import Path
        from django.conf import settings
        from contextlib import redirect_stdout
        from unittest.mock import patch, Mock
        from ui_automation.case_conversion import local_skill
        script=Path(local_skill()['directory'])/'ui_automation_tools.py'
        for action, endpoint in [('list_modules','modules/tree/'),('list_testcases','testcases/')]:
            response=Mock();response.json.return_value={'data':[]}
            with patch.object(sys,'argv',[str(script),'--action',action,'--project_id',str(self.project.pk)]), patch('requests.request',return_value=response) as request, redirect_stdout(io.StringIO()):
                runpy.run_path(str(script),run_name='__main__')
            self.assertTrue(request.call_args.args[1].endswith(endpoint))
        out=io.StringIO()
        with patch.object(sys,'argv',[str(script),'--help']),redirect_stdout(out):
            with self.assertRaises(SystemExit): runpy.run_path(str(script),run_name='__main__')
        self.assertIn('get_ui_modules',out.getvalue())
        self.assertIn('get_testcases',out.getvalue())

    def test_expired_worker_heartbeat_releases_batch_scope(self):
        from datetime import timedelta
        from knowledge_evolution.ui_generation_jobs import reconcile_run
        binding=self.binding()
        binding.generation_run={'status':'running','heartbeat_at':(timezone.now()-timedelta(minutes=10)).isoformat(),'items':[{'source_id':self.case.pk,'status':'exploring'}]}
        binding.save(update_fields=['generation_run'])
        reconcile_run(binding)
        binding.refresh_from_db()
        self.assertEqual(binding.generation_run['status'],'partial_failure')
        self.assertEqual(binding.generation_run['items'][0]['status'],'failed')
