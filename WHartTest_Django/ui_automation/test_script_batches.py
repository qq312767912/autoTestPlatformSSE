from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from projects.models import Project
from .models import UiModule,UiPage,UiPageSteps,UiCaseStepsDetailed,UiTestCase,UiEnvironmentConfig,UiBatchExecutionRecord,UiExecutionRecord
from .script_batches import process_script_batch,execute_one,WaitingForActuator

class ScriptBatchTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='script-batch',password='test')
        self.project=Project.objects.create(name='scripts',creator=self.user)
        module=UiModule.objects.create(project=self.project,name='UI')
        page=UiPage.objects.create(project=self.project,module=module,name='page')
        self.step=UiPageSteps.objects.create(project=self.project,module=module,page=page,name='step')
        self.env=UiEnvironmentConfig.objects.create(project=self.project,name='test',base_url='https://example.com')
        self.cases=[UiTestCase.objects.create(project=self.project,module=module,name=f'case {i}') for i in range(100)]
        UiCaseStepsDetailed.objects.bulk_create([UiCaseStepsDetailed(test_case=c,page_step=self.step) for c in self.cases])
        self.client=APIClient();self.client.force_authenticate(self.user)
        self.dispatch=patch('ui_automation.tasks.run_ui_script_batch.delay',return_value=SimpleNamespace(id='queued')).start()
        self.addCleanup(patch.stopall)

    def create(self,**kwargs):
        body={'project':self.project.pk,'ids':[c.pk for c in self.cases],'env_config_id':self.env.pk,**kwargs}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/api/ui-automation/script-batches/',body,format='json')

    def test_100_cases_queue_without_100_slots_and_continue_after_failure(self):
        response=self.create();self.assertEqual(response.status_code,202,response.data)
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        calls=[]
        def execute(batch,case_id,on_progress):
            calls.append(case_id)
            record=UiExecutionRecord.objects.create(test_case_id=case_id,executor=self.user,status=3 if len(calls)==1 else 2)
            return record,{'message':'real receipt'}
        with patch('ui_automation.script_batches.execute_one',side_effect=execute):process_script_batch(batch.pk)
        batch.refresh_from_db()
        self.assertEqual(calls,[c.pk for c in self.cases])
        self.assertEqual((batch.passed_cases,batch.failed_cases,batch.status),(99,1,3))
        self.assertEqual(batch.execution_records.count(),100)
        self.dispatch.assert_called_once()

    def test_execution_roles_are_transient_and_shared_across_cases(self):
        roles=[{'name':'游客A','guest':True},{'name':'游客B','guest':True}]
        response=self.create(role_matrix=roles)
        self.assertEqual(response.status_code,202,response.data)
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        self.assertTrue(all(len(rows)==2 for rows in batch.run_config['role_snapshots'].values()))
        self.assertTrue(all(c.role_matrix==[] for c in UiTestCase.objects.filter(project=self.project)))

    def test_bulk_configuration_replaces_matrix_for_all_selected_cases(self):
        response=self.client.post('/api/ui-automation/script-batches/configure-roles/',{'project':self.project.pk,'ids':[c.pk for c in self.cases],'role_matrix':[{'name':'游客A','guest':True},{'name':'游客B','guest':True}]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['updated'],100)
        self.assertTrue(all(len(c.role_matrix)==2 for c in UiTestCase.objects.filter(project=self.project)))

    def test_cancel_before_execution_starts_no_cases(self):
        response=self.create();batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        self.client.post(f'/api/ui-automation/script-batches/{batch.pk}/cancel/',{},format='json')
        with patch('ui_automation.script_batches.execute_one') as execute:process_script_batch(batch.pk)
        execute.assert_not_called();batch.refresh_from_db()
        self.assertTrue(all(x['status']=='cancelled' for x in batch.run_items))

    def test_duplicate_active_batch_and_edit_are_rejected(self):
        self.assertEqual(self.create().status_code,202)
        self.assertEqual(self.create().status_code,400)
        response=self.client.post('/api/ui-automation/script-batches/configure-roles/',{'project':self.project.pk,'ids':[self.cases[0].pk],'role_matrix':[]},format='json')
        self.assertEqual(response.status_code,400)

    def test_cross_project_cases_rejected(self):
        project=Project.objects.create(name='other',creator=self.user)
        response=self.create(project=project.pk,env_config_id=self.env.pk)
        self.assertIn(response.status_code,[400,404])

    def test_dispatches_one_case_and_busy_slot_does_not_send_stop(self):
        response=self.create();batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        socket=Mock();socket.recv.return_value='{"code":400,"msg":"执行器空闲 slot 不足","data":{"func_name":"u_test_case","func_args":{}}}'
        with patch('websocket.create_connection',return_value=socket):
            with self.assertRaises(WaitingForActuator):execute_one(batch,self.cases[0].pk,lambda x:None)
        import json
        sent=[json.loads(call.args[0]) for call in socket.send.call_args_list]
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['data']['func_name'],'u_test_case')
        self.assertNotIn('case_ids',sent[0]['data']['func_args'])

    def test_waits_for_one_slot_then_runs_remaining_cases(self):
        response=self.create(ids=[c.pk for c in self.cases[:2]])
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        record=UiExecutionRecord.objects.create(test_case=self.cases[0],executor=self.user,status=2)
        record2=UiExecutionRecord.objects.create(test_case=self.cases[1],executor=self.user,status=2)
        with patch('ui_automation.script_batches.execute_one',side_effect=[WaitingForActuator('slot不足'),(record,{}),(record2,{})]) as execute, patch('ui_automation.script_batches.time.sleep') as sleep:
            process_script_batch(batch.pk)
        self.assertEqual(execute.call_count,3)
        self.assertEqual([c.args[1] for c in execute.call_args_list],[self.cases[0].pk,self.cases[0].pk,self.cases[1].pk])
        sleep.assert_called_once_with(2)
        batch.refresh_from_db();self.assertEqual(batch.status,2)

    def test_scheduler_exception_does_not_leave_batch_queued(self):
        response=self.create(ids=[self.cases[0].pk])
        from .tasks import run_ui_script_batch
        with patch('ui_automation.script_batches.process_script_batch',side_effect=RuntimeError('scheduler fault')):
            with self.assertRaisesMessage(RuntimeError,'scheduler fault'):run_ui_script_batch.run(response.data['id'])
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        self.assertEqual(batch.status,4)
        self.assertEqual(batch.failed_cases,1)
        self.assertIn('scheduler fault',batch.run_items[0]['message'])

    def test_unavailable_executor_timeout_stops_remaining_queue(self):
        response=self.create(ids=[c.pk for c in self.cases[:2]])
        with patch('ui_automation.script_batches.execute_one',side_effect=WaitingForActuator('slot不足')) as execute,patch('ui_automation.script_batches.time.monotonic',side_effect=[0,901]):
            process_script_batch(response.data['id'])
        execute.assert_called_once()
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        self.assertEqual([x['status'] for x in batch.run_items],['failed','cancelled'])

    def test_independent_result_keeps_execution_request_and_owner(self):
        from asgiref.sync import async_to_sync
        from .consumers import UiAutomationConsumer
        consumer=UiAutomationConsumer()
        with patch.object(consumer,'_release_actuator_slots'):
            async_to_sync(consumer.save_execution_result)({'case_id':self.cases[0].pk,'executor_id':self.user.pk,'status':'success','execution_request_id':'paired-result','environment':{'env_config_id':self.env.pk}})
        record=UiExecutionRecord.objects.get(environment__execution_request_id='paired-result')
        self.assertEqual(record.executor_id,self.user.pk)
        self.assertEqual(record.environment['env_config_id'],self.env.pk)

    def test_missing_execution_receipt_stops_later_dispatch(self):
        from .script_batches import ExecutionTimeout
        response=self.create(ids=[c.pk for c in self.cases[:2]])
        with patch('ui_automation.script_batches.execute_one',side_effect=ExecutionTimeout('未返回结果')) as execute:
            process_script_batch(response.data['id'])
        execute.assert_called_once()
        batch=UiBatchExecutionRecord.objects.get(pk=response.data['id'])
        self.assertEqual([x['status'] for x in batch.run_items],['failed','cancelled'])
