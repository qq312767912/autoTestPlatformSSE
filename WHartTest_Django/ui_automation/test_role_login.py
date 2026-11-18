from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from .role_execution import validate_matrix
from .role_login import password_value, login_steps

class RoleAccountTests(SimpleTestCase):
    def role(self):
        return {'name':'主用户','account':'user1','password':'secret-value','captcha':'A1ad',
                'login':{'url':'https://example.com/login','account_selector':'#account','password_selector':'#password',
                         'captcha_selector':'#code','submit_selector':'#submit','success_selector':'#signed-in'}}

    def test_credentials_encrypted_and_reusable(self):
        role=validate_matrix([self.role()],1)[0]
        self.assertNotIn('password',role)
        self.assertNotIn('secret-value',str(role))
        self.assertEqual(password_value(role,1),'secret-value')
        again=validate_matrix([role],1)[0]
        self.assertEqual(password_value(again,1),'secret-value')
        with self.assertRaises(ValidationError): validate_matrix([role],2)

    def test_login_order_and_sensitive_steps(self):
        role=validate_matrix([self.role()],1)[0]
        steps=login_steps(role,1)
        self.assertEqual([s['ope_key'] for s in steps],['goto','fill','fill','fill','click','assert'])
        self.assertEqual([s['ope_value']['text'] for s in steps if s['ope_key']=='fill'],['user1','secret-value','A1ad'])
        self.assertTrue(all(s['sensitive'] for s in steps))

    def test_guest_drops_credentials(self):
        role=self.role()
        role['guest']=True
        self.assertNotIn('password_ref',validate_matrix([role],1)[0])

    def test_incomplete_login_cannot_run(self):
        role=validate_matrix([{'name':'用户','account':'user1'}],1)[0]
        with self.assertRaisesMessage(ValidationError,'补齐登录页'):
            login_steps(role,1)

    def test_no_password_login(self):
        row=self.role()
        row.pop('password')
        role=validate_matrix([row],1)[0]
        self.assertEqual(len(login_steps(role,1)),5)

    def test_invalid_login_config_rejected(self):
        for config in ['invalid',{'url':'javascript:alert(1)'},{'url':'https://example.com','account_selector':'x'*2001}]:
            with self.assertRaises(ValidationError):
                validate_matrix([{'name':'用户','account':'user1','login':config}],1)

    def test_new_password_replaces_saved_reference(self):
        role=validate_matrix([self.role()],1)[0]
        changed=validate_matrix([{**role,'password':'changed-secret'}],1)[0]
        self.assertNotEqual(changed['password_ref'],role['password_ref'])
        self.assertEqual(password_value(changed,1),'changed-secret')

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from projects.models import Project
from .models import UiModule, UiTestCase, UiEnvironmentConfig
from .script_batches import UiScriptBatchViewSet
from .serializers import UiTestCaseSerializer
from .role_execution import execution_roles

class RoleAccountPersistenceTests(TransactionTestCase):
    def test_bulk_configuration_saves_encrypted_credentials_and_runs_without_snapshot(self):
        user=get_user_model().objects.create_superuser(username='role-account',password='test')
        project=Project.objects.create(name='role-account',creator=user)
        env=UiEnvironmentConfig.objects.create(project=project,name='test')
        module=UiModule.objects.create(project=project,name='UI')
        case=UiTestCase.objects.create(project=project,module=module,name='account login')
        row=RoleAccountTests().role()
        request=APIRequestFactory().post('/',{'project':project.pk,'ids':[case.pk],'role_matrix':[row]},format='json')
        force_authenticate(request,user)
        response=UiScriptBatchViewSet.as_view({'post':'configure_roles'})(request)
        self.assertEqual(response.status_code,200,response.data)
        case.refresh_from_db()
        self.assertNotIn('secret-value',str(case.role_matrix))
        self.assertNotIn('password',case.role_matrix[0])
        public=UiTestCaseSerializer(case).data['role_matrix'][0]
        self.assertNotIn('password',public)
        self.assertEqual(password_value(public,project.pk),'secret-value')
        _,roles=execution_roles(case.pk,env.pk,user)
        self.assertEqual(roles[0]['account'],'user1')
        self.assertIsNone(roles[0]['auth_state_id'])
