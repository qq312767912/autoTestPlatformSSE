"""无需Django或Docker即可验证的执行面协议与负向回归。"""
import base64
import hashlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from .executor import JobRequest,SkillRunnerError,execute_job
from .server import build_server

_client_file=Path(__file__).resolve().parent.parent/'orchestrator_integration/builtin_tools/skill_runner_client.py'
spec=importlib.util.spec_from_file_location('runner_client_test',_client_file)
client=importlib.util.module_from_spec(spec);sys.modules[spec.name]=client;spec.loader.exec_module(client)


def package(files):
 return [{'name':name,'data_b64':base64.b64encode(data.encode()).decode(),'sha256':hashlib.sha256(data.encode()).hexdigest()} for name,data in {'SKILL.md':'---\nname: sample\n---\n',**files}.items()]


class JobTests(unittest.TestCase):
 def run_job(self,source,**kw):
  return execute_job(JobRequest(package({'main.py':source}),'python main.py',**kw))
 def test_executes_exact_snapshot_and_does_not_inherit_platform_secrets(self):
  with patch.dict(os.environ,{'POSTGRES_PASSWORD':'hidden','SKILL_RUNNER_TOKEN':'hidden2','DJANGO_SECRET_KEY':'hidden3'}):
   r=self.run_job("import os,json;print(json.dumps({k:os.getenv(k) for k in ['POSTGRES_PASSWORD','SKILL_RUNNER_TOKEN','DJANGO_SECRET_KEY']}))")
  self.assertEqual(r.returncode,0);self.assertEqual(set(json.loads(r.stdout).values()),{None})
 def test_passes_only_runtime_key_and_redacts_stdout(self):
  r=self.run_job("import os;print(os.environ['WHARTTEST_API_KEY'])",runtime_env={'WHARTTEST_API_KEY':'caller-only-key'})
  self.assertNotIn('caller-only-key',r.stdout);self.assertIn('[REDACTED]',r.stdout)
  with self.assertRaises(SkillRunnerError):self.run_job('print(1)',runtime_env={'POSTGRES_PASSWORD':'not-allowed'})
 def test_restores_artifact_and_checks_digest(self):
  r=self.run_job("import os;from pathlib import Path;Path(os.environ['ARTIFACT_DIR'],'结果.txt').write_text('结果',encoding='utf8')")
  with tempfile.TemporaryDirectory() as temp:
   client.restore_artifacts(r.artifacts,temp);self.assertEqual(Path(temp,'结果.txt').read_text(),'结果')
   r.artifacts[0]['sha256']='bad'
   with self.assertRaises(client.SkillRunnerUnavailable):client.restore_artifacts(r.artifacts,temp)
 def test_blocks_package_escape_duplicate_and_tampering(self):
  for name in ['../escape.py','/tmp/escape.py','a/../../x','x\\x.py','x//x.py','x:evil']:
   data=package({name:'print(1)'})
   with self.assertRaises(SkillRunnerError):execute_job(JobRequest(data,'python main.py'))
  data=package({'main.py':'print(1)'});data[-1]['sha256']='bad'
  with self.assertRaises(SkillRunnerError):execute_job(JobRequest(data,'python main.py'))
  data=package({'main.py':'print(1)'});data.append(data[-1])
  with self.assertRaises(SkillRunnerError):execute_job(JobRequest(data,'python main.py'))
 def test_blocks_shell_inline_modules_install_and_external_scripts(self):
  for command in ['sh -c id','python -c "print(1)"','python -m pip install x','python /etc/passwd','python ../main.py','python main.py; id','npm install x','PATH=/tmp python main.py']:
   with self.subTest(command=command),self.assertRaises(SkillRunnerError):execute_job(JobRequest(package({'main.py':'print(1)'}),command))
 def test_temp_workspace_and_background_child_are_removed(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'SKILL_RUNNER_WORKSPACE_ROOT':temp}):
   r=self.run_job("import subprocess,sys;from pathlib import Path;import os;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(40)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid)")
   self.assertFalse(list(Path(temp).iterdir()))
   pid=int(r.stdout.strip())
   # A killed orphan may temporarily remain a zombie until the host init reaps it.
   status=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
   self.assertTrue(not status or status.startswith('Z'),status)
 def test_timeout_is_bounded_and_workspace_removed(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'SKILL_RUNNER_WORKSPACE_ROOT':temp}):
   start=time.monotonic()
   with self.assertRaisesRegex(SkillRunnerError,'超时'):self.run_job('import time;time.sleep(40)',timeout_seconds=1)
   self.assertLess(time.monotonic()-start,5);self.assertFalse(list(Path(temp).iterdir()))
 def test_bounds_stdout(self):
  r=self.run_job("print('x'*400000)")
  self.assertLess(len(r.stdout),270000);self.assertIn('truncated',r.stdout)
 def test_does_not_mutate_source_and_rejects_symlinks(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'SKILL.md').write_text('skill');(root/'main.py').write_text('print(5)')
   payload=client.build_package(temp);execute_job(JobRequest(payload,'python main.py'))
   self.assertEqual((root/'main.py').read_text(),'print(5)')
   (root/'link.py').symlink_to(root/'main.py')
   with self.assertRaises(client.SkillRunnerUnavailable):client.build_package(temp)
 def test_artifact_traversal_rejected_before_any_write(self):
  with tempfile.TemporaryDirectory() as temp:
   payload={'name':'../escape','data_b64':'eA==','size':1,'sha256':hashlib.sha256(b'x').hexdigest()}
   with self.assertRaises(client.SkillRunnerUnavailable):client.restore_artifacts([payload],temp)
   self.assertEqual(list(Path(temp).iterdir()),[])


class HttpTests(unittest.TestCase):
 def setUp(self):
  self.server=build_server('127.0.0.1',0,'test-key');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.server.server_port}'
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
 def request(self,token,body):
  request=urllib.request.Request(self.url+'/execute',data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
  return urllib.request.urlopen(request,timeout=5)
 def test_health_and_missing_wrong_auth(self):
  with urllib.request.urlopen(self.url+'/health') as r:self.assertEqual(r.status,200)
  for token in ['', 'wrong']:
   with self.assertRaises(urllib.error.HTTPError) as ctx:self.request(token,{})
   self.assertEqual(ctx.exception.code,401)
 def test_real_client_server_round_trip(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'SKILL_RUNNER_URL':self.url,'SKILL_RUNNER_TOKEN':'test-key'}):
   Path(temp,'SKILL.md').write_text('skill');Path(temp,'main.py').write_text('print("runner-ok")')
   result=client.submit_job(skill_dir=temp,command='python main.py',timeout_seconds=5)
   self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),'runner-ok')
 def test_busy_is_rejected(self):
  self.assertTrue(self.server.job_gate.acquire())
  try:
   with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('test-key',{})
   self.assertEqual(ctx.exception.code,429)
  finally:self.server.job_gate.release()
 def test_parallel_client_calls_are_serialized_without_busy_failures(self):
  from concurrent.futures import ThreadPoolExecutor
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'SKILL_RUNNER_URL':self.url,'SKILL_RUNNER_TOKEN':'test-key'}):
   Path(temp,'SKILL.md').write_text('skill');Path(temp,'main.py').write_text('import time;time.sleep(.1);print("ok")')
   with ThreadPoolExecutor(max_workers=3) as executor:
    results=list(executor.map(lambda _:client.submit_job(skill_dir=temp,command='python main.py',timeout_seconds=5),range(3)))
   self.assertEqual([r.returncode for r in results],[0,0,0])
 def test_missing_token_configuration_fails_closed(self):
  self.server.expected_token=''
  with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('test-key',{})
  self.assertEqual(ctx.exception.code,401)
 def test_runner_unreachable_never_calls_local_process(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'SKILL_RUNNER_URL':'http://127.0.0.1:1','SKILL_RUNNER_TOKEN':'key'}):
   Path(temp,'SKILL.md').write_text('skill');Path(temp,'main.py').write_text('print(1)')
   with patch('subprocess.Popen') as local,self.assertRaises(client.SkillRunnerUnavailable):client.submit_job(skill_dir=temp,command='python main.py',timeout_seconds=1)
   local.assert_not_called()
 def test_unknown_mode_is_error(self):
  with patch.dict(os.environ,{'SKILL_EXEC_MODE':'runnre'}),self.assertRaises(client.SkillRunnerUnavailable):client.runner_exec_mode()


class BrowserInputTests(unittest.TestCase):
 def test_keeps_http_interactions_and_negative_numeric_fields(self):
  from orchestrator_integration.browser_arguments import validate_browser_arguments
  validate_browser_arguments('open',['https://example.test/path'])
  validate_browser_arguments('fill',['@e1','-1'])
  validate_browser_arguments('close',[])
 def test_blocks_local_file_navigation_and_global_options(self):
  from orchestrator_integration.browser_arguments import validate_browser_arguments
  for command,args in [('open',['file:///etc/passwd']),('open',['javascript:alert(1)']),('open',['https://user:pass@example.test']),('click',['-p','ios']),('close',['--all']),('open',[])]:
   with self.subTest(args=args),self.assertRaises(ValueError):validate_browser_arguments(command,args)


class PayloadCompatibilityTests(unittest.TestCase):
 def test_json_quotes_unicode_and_variable_templates_remain_literal(self):
  payload={'name':"中文 ' 引号",'expression':'${{token}} & query=1'}
  source='import sys,json;print(json.dumps(json.loads(sys.argv[1]),ensure_ascii=False))'
  command=shlex.join(['python','main.py',json.dumps(payload,ensure_ascii=False)])
  result=execute_job(JobRequest(package({'main.py':source}),command))
  self.assertEqual(result.returncode,0);self.assertEqual(json.loads(result.stdout),payload)


class BrowserContextTests(unittest.TestCase):
 def test_same_id_updated_credentials_require_new_context(self):
  from orchestrator_integration.browser_arguments import browser_context_signature
  a=browser_context_signature(1,{'cookies':[{'value':'old'}]},[])
  self.assertEqual(a,browser_context_signature(1,{'cookies':[{'value':'old'}]},[]))
  self.assertNotEqual(a,browser_context_signature(1,{'cookies':[{'value':'new'}]},[]))
  self.assertNotEqual(a,browser_context_signature(None,None,[]))
