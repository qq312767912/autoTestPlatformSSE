"""无Django依赖的入口测试：仅替换ORM/工具装饰器，执行实际路由与产物代码。"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch

class BackendRoutingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.skill_dir=self.root/'version';self.skill_dir.mkdir()
  (self.skill_dir/'SKILL.md').write_text('skill');(self.skill_dir/'main.py').write_text('print(1)')
  settings=SimpleNamespace(MEDIA_ROOT=str(self.root/'media'),MEDIA_URL='/media/',WHARTTEST_API_KEY='platform-admin-key',SKILL_EXEC_MODE='runner')
  def module(name,**kwargs):
   m=ModuleType(name);m.__dict__.update(kwargs);return m
  def decorate(fn):fn.name=fn.__name__;return fn
  key=SimpleNamespace(key='caller-key',id=7,is_valid=lambda:True)
  api=SimpleNamespace(objects=Mock());api.objects.filter.return_value.order_by.return_value=[key]
  self.version=SimpleNamespace(pk='exact-version',is_runnable=True,package_sha256='source-hash',get_full_path=lambda:str(self.skill_dir))
  self.skill=SimpleNamespace(pk=4,name='chosen',get_full_path=lambda:str(self.skill_dir));self.version.skill=self.skill
  model=SimpleNamespace(objects=Mock());model.objects.select_related.return_value.filter.return_value.first.return_value=self.version
  self.service=SimpleNamespace(verify_package_integrity=Mock(return_value={'ok':True}))
  fakes={'django':module('django'),'django.conf':module('django.conf',settings=settings),'django.db':module('django.db',close_old_connections=Mock()),'langchain_core':module('langchain_core'),'langchain_core.tools':module('langchain_core.tools',tool=decorate),'skills':module('skills'),'skills.models':module('skills.models',Skill=Mock(),SkillVersion=model),'skills.versions':module('skills.versions',SkillVersionService=self.service),'api_keys':module('api_keys'),'api_keys.models':module('api_keys.models',APIKey=api)}
  self.patch=patch.dict(sys.modules,fakes);self.patch.start();self.addCleanup(self.patch.stop)
  self.tools=importlib.import_module('orchestrator_integration.builtin_tools.skill_tools')
  self.client=importlib.import_module('orchestrator_integration.builtin_tools.skill_runner_client')
  self.settings_patch=patch.object(self.tools,"settings",settings);self.settings_patch.start();self.addCleanup(self.settings_patch.stop)
 def tool(self):return self.tools.get_skill_tools(user_id=3,project_id=8,chat_session_id='chat',selected_skill_version_id='exact-version')[1]
 def test_routes_exact_version_with_only_caller_key_and_new_artifact_directory(self):
  outcome=self.client.RunnerJobOutcome(0,'done','',1,[],[])
  with patch.object(self.client,'submit_job',return_value=outcome) as submit,patch.object(self.tools.subprocess,'run') as local:
   result=self.tool()(skill_name='chosen',command='python main.py')
  self.assertEqual(result,'done');local.assert_not_called()
  kw=submit.call_args.kwargs
  self.assertEqual(kw['skill_version_id'],'exact-version');self.assertEqual(kw['source_sha256'],'source-hash')
  self.assertEqual(kw['runtime_env']['WHARTTEST_API_KEY'],'caller-key');self.assertNotIn('platform-admin-key',json.dumps(kw))
 def test_package_tampering_rejects_before_submit(self):
  self.service.verify_package_integrity.return_value={'ok':False}
  with patch.object(self.client,'submit_job') as submit:
   result=self.tool()(skill_name='chosen',command='python main.py')
  self.assertIn('完整性',result);submit.assert_not_called()
 def test_unavailable_runner_does_not_fallback_to_backend_execution(self):
  with patch.object(self.client,'submit_job',side_effect=self.client.SkillRunnerUnavailable('offline')),patch.object(self.tools.subprocess,'run') as local:
   result=self.tool()(skill_name='chosen',command='python main.py')
  self.assertIn('offline',result);local.assert_not_called()
 def test_browser_legacy_execution_is_directed_to_platform_tool(self):
  with patch.object(self.client,'submit_job') as submit:
   result=self.tool()(skill_name='chosen',command='node run.js script.js',session_id='browser')
  self.assertIn('platform_browser',result);submit.assert_not_called()

 def test_runner_stdout_cannot_induce_backend_to_copy_unrelated_file(self):
  secret=self.root/'server-secret.json';secret.write_text('never-return-this-content')
  outcome=self.client.RunnerJobOutcome(0,str(secret),'',1,[],[])
  with patch.object(self.client,'submit_job',return_value=outcome):
   result=self.tool()(skill_name='chosen',command='python main.py')
  self.assertNotIn('never-return-this-content',result)
  self.assertFalse(list((self.root/'media').rglob('server-secret.json')))

 def test_quarantined_version_rejects_before_submit(self):
  self.version.is_runnable=False
  with patch.object(self.client,'submit_job') as submit:
   result=self.tool()(skill_name='chosen',command='python main.py')
  self.assertIn('不可运行',result);submit.assert_not_called()
