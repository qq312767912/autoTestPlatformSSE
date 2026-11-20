import importlib.util,json,pathlib,tempfile,unittest
from unittest.mock import patch,Mock
s=importlib.util.spec_from_file_location('assets',pathlib.Path(__file__).parent/'deploy_env/prepare_assets.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class AssetTests(unittest.TestCase):
 def exercise(self,missing=False,conflict=False):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);here=root/'deploy_env';here.mkdir();b=root/'backups/test';b.mkdir(parents=True);source=here/'secrets/actuator_api_password';source.parent.mkdir()
   if not missing:source.write_text('original-password')
   else:
    source.mkdir();old=root/'deploy_env.bak/secrets';old.mkdir(parents=True);(old/source.name).write_text('original-password')
   if conflict:
    extra=root/'secrets';extra.mkdir();(extra/source.name).write_text('different-password')
   c={'services':{'actuator-01':{'volumes':[{'type':'bind','source':str(source),'target':'/run/secrets/actuator_api_password'}]}}}
   for name in ['compose.runtime.json','compose.rollback.json']:(b/name).write_text(json.dumps(c))
   with patch.object(m.subprocess,'run',return_value=Mock(returncode=1,stdout=b'')):
    if conflict:
     import sys,types
     fake=types.SimpleNamespace(select_password=Mock(side_effect=RuntimeError('invalid credentials')))
     with patch.dict(sys.modules,{'credential_check':fake}):
      with self.assertRaises(RuntimeError):m.prepare(b,here)
     self.assertEqual(json.loads((b/'compose.runtime.json').read_text()),c);return
    m.prepare(b,here)
    actual=json.loads((b/'compose.runtime.json').read_text())['services']['actuator-01']['volumes'][0]['source']
    self.assertEqual(pathlib.Path(actual).read_text(),'original-password')
    count=len(list((b/'deployment-assets').iterdir()));m.prepare(b,here)
    self.assertEqual(len(list((b/'deployment-assets').iterdir())),count)
 def test_valid_secret_becomes_independent_and_idempotent(self):self.exercise()
 def test_directory_source_recovers_from_previous_release(self):self.exercise(missing=True)
 def test_conflicting_candidates_stop_before_config_changes(self):self.exercise(missing=True,conflict=True)
if __name__=='__main__':unittest.main()
