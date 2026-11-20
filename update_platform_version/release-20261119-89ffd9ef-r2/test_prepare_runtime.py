import importlib.util,json,pathlib,tempfile,unittest
from unittest.mock import patch
s=importlib.util.spec_from_file_location('runtime',pathlib.Path(__file__).parent/'deploy_env/prepare_runtime.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class RuntimeTests(unittest.TestCase):
 def test_token_preserved_or_generated_and_shared_idempotently(self):
  for original in ['', 'existing-token']:
   with tempfile.TemporaryDirectory() as d:
    p=pathlib.Path(d);c={'services':{'backend':{'environment':{'UI_WS_ACTUATOR_TOKEN':original}},**{k:{'environment':{}} for k in ['actuator-01','actuator-02','actuator-03']}}}
    (p/'compose.runtime.json').write_text(json.dumps(c));(p/'rollback-sentinel').write_text('unchanged')
    m.sync_tokens(p);a=json.loads((p/'compose.runtime.json').read_text());token=a['services']['backend']['environment']['UI_WS_ACTUATOR_TOKEN'];self.assertTrue(token)
    if original:self.assertEqual(token,original)
    for k in ['actuator-01','actuator-02','actuator-03']:self.assertEqual(a['services'][k]['environment']['WHARTTEST_ACTUATOR_WS_TOKEN'],token)
    m.sync_tokens(p);self.assertEqual(json.loads((p/'compose.runtime.json').read_text()),a)
 def test_missing_mcp_file_directory_is_preserved_and_replaced(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);here=root/'new';here.mkdir();(here/'playwright-mcp-entrypoint.sh').write_text('#!/bin/sh\nexit 0\n');source=root/'old'/'playwright-mcp-entrypoint.sh';source.mkdir(parents=True)
   obj=[{'Mounts':[{'Type':'bind','Destination':'/opt/wharttest/playwright-mcp-entrypoint.sh','Source':str(source)}]}]
   with patch.object(m.subprocess,'check_output',return_value=json.dumps(obj).encode()):m.protect_mcp(here);m.protect_mcp(here)
   self.assertTrue(source.is_file());self.assertEqual(len(list(source.parent.glob('*.invalid-*'))),1)
if __name__=='__main__':unittest.main()
