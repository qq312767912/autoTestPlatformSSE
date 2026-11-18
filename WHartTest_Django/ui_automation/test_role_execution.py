from unittest.mock import patch
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from .role_execution import validate_matrix, execution_roles

class RoleConfigurationTests(SimpleTestCase):
    def test_guests_need_no_login_state(self):
        self.assertEqual(validate_matrix([{'name':'游客','guest':True}],1),[{'name':'游客','guest':True,'auth_state_id':None}])

    def test_missing_state_can_be_saved_but_not_executed(self):
        rows=validate_matrix([{'name':'认证用户'}],1)
        with patch('ui_automation.role_execution.UiEnvironmentConfig.objects.filter') as env, patch('ui_automation.role_execution.UiTestCase.objects.get') as get, patch('ui_automation.role_execution.UiAuthState.objects.filter') as auth:
            get.return_value.role_matrix=rows
            get.return_value.project_id=1
            auth.return_value.first.return_value=None
            with self.assertRaisesMessage(ValidationError,'缺少当前环境可用登录态'):
                execution_roles(1,1)

    def test_duplicate_and_invalid_guest_rejected(self):
        for rows in [[{'name':'角色'},{'name':'角色'}],[{'name':'游客','guest':True,'auth_state_id':2}],[{'name':'角色','guest':'false'}]]:
            with self.assertRaises(ValidationError):validate_matrix(rows,1)

    def test_cross_project_state_rejected(self):
        with patch('ui_automation.role_execution.UiAuthState.objects.filter') as auth:
            auth.return_value.exists.return_value=False
            with self.assertRaisesMessage(ValidationError,'不属于当前项目'):
                validate_matrix([{'name':'主用户','auth_state_id':2}],1)

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from projects.models import Project
from .models import UiModule, UiTestCase, UiExecutionRecord, UiEnvironmentConfig
from .role_execution import run_roles
from asgiref.sync import sync_to_async
from unittest.mock import AsyncMock

class RoleRunTests(TransactionTestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='roles',password='test')
        project=Project.objects.create(name='roles',creator=self.user)
        self.env=UiEnvironmentConfig.objects.create(project=project,name='env',base_url='https://example.com')
        module=UiModule.objects.create(project=project,name='UI')
        self.case=UiTestCase.objects.create(project=project,module=module,name='role case',role_matrix=[{'name':'游客A','guest':True},{'name':'游客B','guest':True}])

    async def test_failed_role_does_not_stop_next_or_become_overall_success(self):
        from types import SimpleNamespace
        calls=[]
        async def execute(kind,args,user):
            calls.append(args)
            await sync_to_async(UiExecutionRecord.objects.create)(test_case_id=self.case.pk,status=3 if len(calls)==1 else 2,environment={'execution_request_id':args['execution_request_id']})
        consumer=SimpleNamespace(scope={'user':self.user},send_json=AsyncMock(),_execute_via_recorder=execute)
        await run_roles(consumer,{'case_id':self.case.pk,'env_config_id':self.env.pk},'roles')
        self.assertEqual(len(calls),2)
        self.assertNotEqual(calls[0]['execution_request_id'],calls[1]['execution_request_id'])
        self.assertIsNone(calls[0]['role_auth_state_id'])
        summary=consumer.send_json.call_args.args[0].data.func_args
        self.assertEqual(summary['status'],'failed')
        self.assertTrue(summary['role_complete'])
        self.assertEqual([x['status'] for x in summary['role_results']],['failed','success'])

    async def test_all_required_roles_must_succeed(self):
        from types import SimpleNamespace
        async def execute(kind,args,user):
            await sync_to_async(UiExecutionRecord.objects.create)(test_case_id=self.case.pk,status=2,environment={'execution_request_id':args['execution_request_id']})
        consumer=SimpleNamespace(scope={'user':self.user},send_json=AsyncMock(),_execute_via_recorder=execute)
        await run_roles(consumer,{'case_id':self.case.pk,'env_config_id':self.env.pk},'roles')
        summary=consumer.send_json.call_args.args[0].data.func_args
        self.assertEqual(summary['status'],'success')
        self.assertEqual(len(summary['role_results']),2)

    async def test_overall_record_preserves_failure_when_last_role_passes(self):
        from types import SimpleNamespace
        calls=[]
        async def execute(kind,args,user):
            calls.append(args)
            await sync_to_async(UiExecutionRecord.objects.create)(test_case_id=self.case.pk,status=3 if len(calls)==1 else 2,environment={'execution_request_id':args['execution_request_id']})
        consumer=SimpleNamespace(scope={'user':self.user},send_json=AsyncMock(),_execute_via_recorder=execute)
        await run_roles(consumer,{'case_id':self.case.pk,'env_config_id':self.env.pk,'execution_request_id':'parent-receipt'},'roles')
        self.assertEqual(consumer.send_json.call_args.args[0].data.func_args['execution_request_id'],'parent-receipt')
        record=await sync_to_async(UiExecutionRecord.objects.get)(environment__execution_request_id='parent-receipt')
        self.assertEqual(record.status,3)
        self.assertEqual(len(record.step_results),2)
