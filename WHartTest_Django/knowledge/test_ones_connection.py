from unittest.mock import patch,Mock
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
import requests
from .ones_import import normalize_ones_url,probe_ones,OnesClient
class OnesConnectionTests(SimpleTestCase):
 def test_default_and_validation(self):
  self.assertEqual(OnesClient.base,'https://10.122.212.49:60000')
  self.assertEqual(normalize_ones_url(OnesClient.base+'/'),OnesClient.base)
  for value in ['file:///etc/passwd','https://u:p@host','https://host/path','https://host?key=x',None]:
   with self.assertRaises(ValidationError):normalize_ones_url(value)
 def test_reachable_auth_endpoint_is_not_login_success(self):
  with patch('knowledge.ones_import.requests.Session') as session:
   response=session.return_value.__enter__.return_value.get.return_value.__enter__.return_value;response.status_code=405
   result=probe_ones(OnesClient.base);self.assertTrue(result['connected']);self.assertIn('登录验证',result['message'])
 def test_certificate_error_is_not_bypassed(self):
  with patch('knowledge.ones_import.requests.Session') as session:
   session.return_value.__enter__.return_value.get.side_effect=requests.exceptions.SSLError()
   self.assertIn('证书',probe_ones(OnesClient.base)['message'])

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import KnowledgeGlobalConfig
from .views import KnowledgeGlobalConfigView

class OnesSettingsTests(TestCase):
 def setUp(self):
  self.admin=get_user_model().objects.create_user(username='ones-admin',is_superuser=True)
  self.user=get_user_model().objects.create_user(username='ones-user')
  self.factory=APIRequestFactory()
 def call(self,method,data,user):
  r=getattr(self.factory,method)('/api/knowledge/global-config/',data,format='json');force_authenticate(r,user=user)
  return KnowledgeGlobalConfigView.as_view()(r)
 def test_default_and_saved_address_persist(self):
  self.assertEqual(KnowledgeGlobalConfig.get_config().ones_base_url,'https://10.122.212.49:60000')
  response=self.call('put',{'ones_base_url':'https://10.122.212.50:60000/'},self.admin)
  self.assertEqual(response.status_code,200)
  self.assertEqual(KnowledgeGlobalConfig.get_config().ones_base_url,'https://10.122.212.50:60000')
 def test_only_admin_can_save_or_probe(self):
  for method in ['put','post']:
   self.assertEqual(self.call(method,{'ones_base_url':'https://10.0.0.1'},self.user).status_code,403)
 def test_probe_does_not_save_unsaved_address(self):
  with patch('knowledge.ones_import.probe_ones',return_value={'connected':True}) as probe:
   self.assertEqual(self.call('post',{'ones_base_url':'https://10.0.0.2'},self.admin).status_code,200)
   probe.assert_called_once_with('https://10.0.0.2')
  self.assertEqual(KnowledgeGlobalConfig.get_config().ones_base_url,'https://10.122.212.49:60000')

class OnesClientRequestTests(SimpleTestCase):
 def test_saved_address_is_used_for_login(self):
  with patch('knowledge.ones_import.requests.Session') as session:
   response=session.return_value.request.return_value;response.status_code=200;response.json.return_value={'user':{'uuid':'user','token':'test-token'}}
   client=OnesClient('user@example.com','test-password','https://10.0.0.5:60000')
   self.assertEqual(session.return_value.request.call_args.args[1],'https://10.0.0.5:60000/project/api/project/auth/login')
   client.session.close()
 def test_invalid_json_is_not_reported_as_network_failure(self):
  with patch('knowledge.ones_import.requests.Session') as session:
   response=session.return_value.request.return_value;response.status_code=200;response.json.side_effect=requests.exceptions.JSONDecodeError('invalid','',0)
   with self.assertRaisesRegex(ValueError,'无效数据'):OnesClient('user@example.com','test-password')
