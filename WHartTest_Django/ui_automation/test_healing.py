from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from django.utils import timezone
from rest_framework.test import APIClient
from orchestrator_integration.test_api_generation import ApiGenerationBindingTests, ToolRequest
from orchestrator_integration.generation_guard import UiHealingGuardMiddleware
from knowledge_evolution.hub_assessment_models import SkillAgentTaskBinding
from .models import UiModule, UiPage, UiPageSteps, UiPageStepsDetailed, UiTestCase, UiCaseStepsDetailed, UiEnvironmentConfig, UiExecutionRecord
from .healing import process_batch


class UiHealingTests(ApiGenerationBindingTests):
    def setUp(self):
        super().setUp()
        self.user.is_superuser=True; self.user.save()
        self.client=APIClient(); self.client.force_authenticate(self.user)
        self.module=UiModule.objects.create(project=self.project,name='UI')
        self.page=UiPage.objects.create(project=self.project,module=self.module,name='page')
        self.step=UiPageSteps.objects.create(project=self.project,module=self.module,page=self.page,name='steps')
        self.assertion=UiPageStepsDetailed.objects.create(page_step=self.step,step_type=1,ope_key='assert_visible')
        self.case=UiTestCase.objects.create(project=self.project,module=self.module,name='case')
        UiCaseStepsDetailed.objects.create(test_case=self.case,page_step=self.step)
        self.env=UiEnvironmentConfig.objects.create(project=self.project,name='env',base_url='https://example.com')
        resolver=patch('ui_automation.healing.healing_version',return_value=self.version)
        resolver.start(); self.addCleanup(resolver.stop)

    def create(self, **extra):
        body={'project':self.project.pk,'ids':[self.case.pk],'env_config_id':self.env.pk,**extra}
        with self.captureOnCommitCallbacks(execute=True), patch('ui_automation.tasks.run_ui_healing.delay'):
            return self.client.post('/api/ui-automation/healing-batches/',body,format='json')

    def batch(self):
        response=self.create(); self.assertEqual(response.status_code,202,response.data)
        return SkillAgentTaskBinding.objects.get(generation_mode='ui_healing')

    def test_rejects_foreign_cases_drafts_and_missing_permissions(self):
        self.assertEqual(self.create(ids=[999999]).status_code,400)
        self.assertEqual(self.create(ids=[True]).status_code,400)
        self.assertion.delete()
        self.assertEqual(self.create().status_code,400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.create().status_code,403)
        self.assertFalse(SkillAgentTaskBinding.objects.exists())

    def test_conflicting_batch_and_owner_isolation(self):
        batch=self.batch()
        self.assertEqual(self.create().status_code,409)
        self.client.force_authenticate(self.other)
        response=self.client.get('/api/ui-automation/healing-batches/',{'project':self.project.pk,'session_id':batch.session.session_id})
        self.assertEqual(response.status_code,403)

    def test_first_pass_does_not_call_ai(self):
        batch=self.batch()
        with patch('ui_automation.healing.execute_case',return_value=SimpleNamespace(pk=11,status=2)), patch('ui_automation.healing.consume',new_callable=AsyncMock) as ai, patch('orchestrator_integration.ui_generation_browser.run_ui_browser'):
            process_batch(batch.pk)
        ai.assert_not_called(); batch.refresh_from_db()
        self.assertEqual(batch.generation_run['status'],'completed')
        self.assertEqual(batch.generation_run['items'][0]['status'],'passed')

    def test_failed_script_repairs_once_and_requires_successful_retry(self):
        for retry_status in (2,3):
            SkillAgentTaskBinding.objects.all().delete()
            batch=self.batch()
            records=[SimpleNamespace(pk=11,status=3,step_results=[{'status':'failed'}]),SimpleNamespace(pk=12,status=retry_status,error_message='still broken')]
            with patch('ui_automation.healing.execute_case',side_effect=records) as execute, patch('ui_automation.healing.consume',new_callable=AsyncMock) as ai, patch('orchestrator_integration.ui_generation_browser.run_ui_browser'):
                process_batch(batch.pk)
            self.assertEqual(execute.call_count,2); self.assertEqual(ai.call_count,1)
            batch.refresh_from_db(); item=batch.generation_run['items'][0]
            self.assertEqual(item['status'],'healed' if retry_status==2 else 'failed')
            self.assertEqual(item['retry_record_id'],12)

    def test_assertion_change_is_not_accepted_as_healing(self):
        batch=self.batch()
        async def weaken(*args,**kwargs):
            from asgiref.sync import sync_to_async
            await sync_to_async(UiPageStepsDetailed.objects.filter(pk=self.assertion.pk).update)(ope_key='assert_empty')
        with patch('ui_automation.healing.execute_case',return_value=SimpleNamespace(pk=11,status=3,step_results=[{'status':'failed'}])) as execute, patch('ui_automation.healing.consume',side_effect=weaken), patch('orchestrator_integration.ui_generation_browser.run_ui_browser'):
            process_batch(batch.pk)
        self.assertEqual(execute.call_count,1); batch.refresh_from_db()
        self.assertEqual(batch.generation_run['items'][0]['status'],'failed')
        self.assertIn('断言',batch.generation_run['items'][0]['message'])

    def test_cancel_retains_results_and_does_not_run_queued_cases(self):
        batch=self.batch(); batch.generation_run['cancel_requested']=True; batch.save()
        with patch('ui_automation.healing.execute_case') as execute:
            process_batch(batch.pk)
        execute.assert_not_called();batch.refresh_from_db()
        self.assertEqual(batch.generation_run['status'],'cancelled')
        self.assertEqual(batch.generation_run['items'][0]['status'],'cancelled')

    def test_guard_blocks_execution_and_destructive_actions(self):
        handler=AsyncMock()
        from unittest.mock import Mock
        handler=Mock()
        guard=UiHealingGuardMiddleware('ui_healing',self.project.pk,'session')
        for action in ['execute_testcase','delete_element','copy_testcase','create_testcase','bind_generated_ui_case']:
            request=ToolRequest({'id':'a','name':'execute_skill_script','args':{'command':f'python tools.py --action={action}'}})
            self.assertEqual(guard.wrap_tool_call(request,handler).status,'error')
        handler.assert_not_called()


    def test_step_edits_cannot_remove_assertions_or_touch_unselected_steps(self):
        import json, shlex
        from unittest.mock import Mock
        batch=self.batch()
        batch.generation_run['active_source_id']=self.case.pk
        batch.save()
        guard=UiHealingGuardMiddleware('ui_healing',self.project.pk,batch.session.session_id)
        handler=Mock(return_value='allowed')
        def request(steps, step_id=None):
            cmd=shlex.join(['python','tools.py','--action','set_step_details','--step_id',str(step_id or self.step.pk),'--steps',json.dumps(steps)])
            return ToolRequest({'id':'scope','name':'execute_skill_script','args':{'command':cmd}})
        self.assertEqual(guard.wrap_tool_call(request([]),handler).status,'error')
        valid=[{'step_type':1,'element':None,'ope_key':'assert_visible','ope_value':None}]
        self.assertEqual(guard.wrap_tool_call(request(valid,99999),handler).status,'error')
        handler.assert_not_called()
        self.assertEqual(guard.wrap_tool_call(request(valid),handler),'allowed')


    def test_script_result_is_paired_with_request_and_persisted_record(self):
        import json
        from .healing import execute_case
        batch=self.batch()
        class Socket:
            def send(inner, payload):
                args=json.loads(payload)['data']['func_args']
                inner.request_id=args['execution_request_id']
                UiExecutionRecord.objects.create(test_case=self.case, executor=self.user, status=2, start_time=timezone.now(),
                    environment={'env_config_id':self.env.pk,'execution_request_id':inner.request_id})
                inner.messages=[{'data':{'func_name':'u_case_result','func_args':{'execution_request_id':'unrelated','status':'success'}}},
                    {'data':{'func_name':'u_case_result','func_args':{'execution_request_id':inner.request_id,'status':'success'}}}]
            def recv(inner): return json.dumps(inner.messages.pop(0))
            def close(inner): pass
        with patch('websocket.create_connection',return_value=Socket()):
            record=execute_case(batch,self.case.pk)
        self.assertEqual(record.status,2)
        self.assertEqual(record.environment['env_config_id'],self.env.pk)

    def test_cancel_queued_batch_releases_conflict_without_worker(self):
        batch=self.batch()
        response=self.client.post('/api/ui-automation/healing-batches/cancel/',{'project':self.project.pk,'session_id':batch.session.session_id},format='json')
        self.assertEqual(response.status_code,200)
        batch.refresh_from_db();self.assertEqual(batch.generation_run['status'],'cancelled')
        self.assertEqual(self.create().status_code,202)

    def test_currently_running_case_cannot_be_submitted(self):
        self.case.status=1;self.case.save()
        self.assertEqual(self.create().status_code,400)
