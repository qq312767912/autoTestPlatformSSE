import importlib.util,json,pathlib,unittest
from unittest.mock import patch,Mock
s=importlib.util.spec_from_file_location('credential',pathlib.Path(__file__).parent/'deploy_env/credential_check.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class CredentialTests(unittest.TestCase):
 def test_password_passed_only_on_stdin(self):
  config={'services':{'actuator-01':{'environment':{'WHARTTEST_ACTUATOR_API_USERNAME':'admin'}},'backend':{'image':'test-image','networks':{'db-net':{}},'environment':{'DATABASE_TYPE':'postgres','POSTGRES_PASSWORD':'db-secret'}}}}
  with patch.object(m.subprocess,'check_output',return_value=json.dumps([{'NetworkSettings':{'Networks':{'db-net':{}}}}]).encode()),patch.object(m.subprocess,'run',return_value=Mock(returncode=0)) as run,patch.object(m.getpass,'getpass') as prompt:
   self.assertEqual(m.select_password(config,[b'actor-secret']),b'actor-secret');prompt.assert_not_called()
   self.assertNotIn('actor-secret',repr(run.call_args.args));self.assertEqual(json.loads(run.call_args.kwargs['input'])['password'],'actor-secret')
if __name__=='__main__':unittest.main()
