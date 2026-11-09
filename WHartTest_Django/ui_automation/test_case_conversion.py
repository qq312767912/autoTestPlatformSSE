import tempfile
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth.models import User, Permission
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from projects.models import Project, ProjectMember
from testcases.models import TestCase as BusinessCase, TestCaseModule, TestCaseStep
from .models import UiModule, UiTestCase, UiCaseStepsDetailed
from .case_conversion import local_skill, source_snapshot

BASE='/api/ui-automation/testcases/'

class BusinessCaseConversionTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        skill=Path(self.tmp.name)/'excel-to-ui-case';(skill/'references').mkdir(parents=True)
        (skill/'SKILL.md').write_text('读取源用例，保存待采集草稿。[契约](references/platform-contract.md)')
        (skill/'references/platform-contract.md').write_text('不得编造定位器，不自动执行。')
        self.settings_override=override_settings(BUNDLED_SKILLS_DIR=self.tmp.name)
        self.settings_override.enable();self.addCleanup(self.settings_override.disable)
        self.user=User.objects.create_superuser('converter',password='test')
        self.project=Project.objects.create(name='转换项目',creator=self.user)
        self.other=Project.objects.create(name='其他项目')
        self.module=UiModule.objects.create(project=self.project,name='UI模块',creator=self.user)
        self.business_module=TestCaseModule.objects.create(project=self.project,name='业务模块',creator=self.user)
        self.source=BusinessCase.objects.create(project=self.project,module=self.business_module,name='游客登录引导',precondition='角色：游客；密码：private-value',notes='保留全部检查点',level='P1',creator=self.user)
        self.step=TestCaseStep.objects.create(test_case=self.source,step_number=1,description='点击登录',expected_result='显示登录对话框',creator=self.user)
        self.client=APIClient();self.client.force_authenticate(self.user)

    def converted(self):
        return [{'source_id':self.source.pk,'role':'游客','name':'登录引导','preconditions':'游客访问首页','steps':[{'action':'点击登录','expected_result':'显示登录对话框'}],'gaps':['缺少实际页面地址']}],{'id':1,'name':'test-model'}

    def preview(self):
        with patch('ui_automation.case_conversion.convert_with_skill',return_value=self.converted()):
            return self.client.post(BASE+'conversion-preview/',{'project':self.project.pk,'module':self.module.pk,'source_ids':[self.source.pk]},format='json')

    def test_source_listing_project_scope_and_skill_provenance(self):
        r=self.client.get(BASE+'conversion-sources/',{'project':self.project.pk,'search':'游客'})
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['count'],1)
        self.assertEqual(r.data['skill']['name'],'excel-to-ui-case')
        self.assertNotIn('private-value',str(r.data))

    def test_preview_and_apply_preserve_original_and_create_only_draft(self):
        preview=self.preview();self.assertEqual(preview.status_code,200,preview.data)
        self.assertFalse(UiTestCase.objects.exists())
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview.data['preview_token']},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['created_count'],1)
        target=UiTestCase.objects.get();self.assertTrue(target.name.startswith('[待采集]'))
        self.assertIn('显示登录对话框',target.description)
        self.assertNotIn('source_key',target.description)
        self.assertNotIn('SHA256',target.description)
        self.assertNotIn('excel-to-ui-case',target.description)
        self.assertEqual(target.conversion_metadata['source']['id'],self.source.pk)
        self.assertEqual(target.conversion_metadata['skill']['name'],'excel-to-ui-case')
        self.assertNotIn('private-value',str(target.conversion_metadata))
        detail=self.client.get(BASE+str(target.pk)+'/')
        self.assertNotIn('conversion_metadata',detail.data)
        self.assertNotIn('private-value',target.description)
        self.assertEqual(target.status,0)
        self.assertFalse(UiCaseStepsDetailed.objects.exists())
        self.source.refresh_from_db();self.assertIsNone(self.source.ui_test_case_id)
        self.assertEqual(self.source.precondition,'角色：游客；密码：private-value')

    def test_confirm_replay_reuses_without_overwriting_manual_changes(self):
        preview=self.preview().data
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json')
        self.assertEqual(r.status_code,200,r.data)
        target=UiTestCase.objects.get();target.name='人工修改名称';target.description='人工修改描述';target.save()
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json')
        self.assertEqual(r.data['reused_count'],1)
        self.assertEqual(UiTestCase.objects.count(),1)
        self.assertEqual(UiTestCase.objects.get().name,'人工修改名称')
        self.assertEqual(UiTestCase.objects.get().description,'人工修改描述')

    def test_legacy_metadata_is_migrated_and_replay_still_reuses(self):
        import hashlib, importlib, json
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        preview=self.preview().data
        source,fingerprint=source_snapshot(self.source)
        identity=f'testcase-management|{self.project.pk}|{self.module.pk}|{self.source.pk}|游客'
        marker='[business-to-ui:'+hashlib.sha256(identity.encode()).hexdigest()+']'
        old='\n'.join([marker,'[excel-to-ui-case]','source_key='+identity,
            '来源：测试用例管理 / TC1 / 源用例',
            'Skill：excel-to-ui-case；版本 SHA256：'+local_skill()['sha256'],
            '转换模型：test-model','来源内容SHA256='+fingerprint,'角色：游客',
            '转换状态：待采集；执行情况：未执行','前置条件：游客访问首页',
            '转换步骤：','1. 点击登录','预期：显示登录对话框','缺口：未执行验证',
            '原始业务用例（完整保留，凭据脱敏）：',json.dumps(source,ensure_ascii=False)])
        case=UiTestCase.objects.create(project=self.project,module=self.module,name='已有用例',description=old)
        manual=UiTestCase.objects.create(project=self.project,module=self.module,name='手工用例',description='SHA256 是业务检查内容')
        migration=importlib.import_module('ui_automation.migrations.0024_uitestcase_conversion_metadata')
        migration.separate_legacy_descriptions(apps,SimpleNamespace(connection=connection))
        case.refresh_from_db();manual.refresh_from_db()
        self.assertEqual(case.description,'角色：游客\n前置条件：游客访问首页\n操作步骤：\n1. 点击登录\n预期：显示登录对话框')
        self.assertEqual(case.conversion_metadata['legacy_description'],old)
        self.assertEqual(manual.description,'SHA256 是业务检查内容')
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json')
        self.assertEqual(r.data['reused_count'],1)
        self.assertFalse(r.data['results'][0]['source_changed'])
        case.description='人工编辑业务描述';case.save()
        migration.restore_legacy_descriptions(apps,SimpleNamespace(connection=connection))
        case.refresh_from_db();self.assertEqual(case.description,'人工编辑业务描述')

    def test_source_or_skill_change_invalidates_preview(self):
        preview=self.preview().data
        self.step.expected_result='新的预期';self.step.save()
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json')
        self.assertEqual(r.status_code,400)
        self.assertFalse(UiTestCase.objects.exists())
        preview=self.preview().data
        path=Path(self.tmp.name)/'excel-to-ui-case/SKILL.md';path.write_text(path.read_text()+'更新')
        r=self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json')
        self.assertEqual(r.status_code,400)

    def test_forged_or_other_user_token_is_rejected(self):
        preview=self.preview().data
        token=preview['preview_token']+'tampered'
        self.assertEqual(self.client.post(BASE+'conversion-apply/',{'preview_token':token},format='json').status_code,400)
        other=User.objects.create_superuser('other-admin',password='test')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(BASE+'conversion-apply/',{'preview_token':preview['preview_token']},format='json').status_code,403)
        self.assertFalse(UiTestCase.objects.exists())

    def test_cross_project_source_and_target_rejected(self):
        other_module=UiModule.objects.create(project=self.other,name='外部模块')
        r=self.client.post(BASE+'conversion-preview/',{'project':self.project.pk,'module':other_module.pk,'source_ids':[self.source.pk]},format='json')
        self.assertEqual(r.status_code,400)
        r=self.client.post(BASE+'conversion-preview/',{'project':self.other.pk,'module':other_module.pk,'source_ids':[self.source.pk]},format='json')
        self.assertEqual(r.status_code,400)

    def test_nonmember_with_model_permissions_is_denied(self):
        user=User.objects.create_user('outsider',password='test')
        user.user_permissions.add(*Permission.objects.filter(codename__in=['view_uitestcase','add_uitestcase','view_testcase']))
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(BASE+'conversion-sources/',{'project':self.project.pk}).status_code,403)

    def test_missing_skill_fails_explicitly(self):
        (Path(self.tmp.name)/'excel-to-ui-case/SKILL.md').unlink()
        r=self.preview();self.assertEqual(r.status_code,400)
        self.assertIn('尚未部署',str(r.data))

    def test_snapshot_redaction_does_not_damage_json_structure(self):
        snapshot,fingerprint=source_snapshot(self.source)
        self.assertEqual(snapshot['steps'][0]['expected_result'],'显示登录对话框')
        self.assertNotIn('private-value',str(snapshot))
        self.assertEqual(len(fingerprint),64)

    def test_omitted_source_is_preserved_as_explicit_incomplete_draft(self):
        with patch('ui_automation.case_conversion.convert_with_skill',return_value=([],{'id':1,'name':'test-model'})):
            r=self.client.post(BASE+'conversion-preview/',{'project':self.project.pk,'module':self.module.pk,'source_ids':[self.source.pk]},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['source_count'],1)
        self.assertTrue(r.data['entries'][0]['name'].startswith('[待补充]'))
        self.assertEqual(r.data['entries'][0]['steps'][0]['expected_result'],'显示登录对话框')
        self.assertIn('模型未返回',r.data['entries'][0]['gaps'][0])

    def test_role_split_and_expired_preview(self):
        converted,model=self.converted()
        converted.append({**converted[0],'role':'已登录用户','name':'登录态保持'})
        with patch('ui_automation.case_conversion.convert_with_skill',return_value=(converted,model)):
            r=self.client.post(BASE+'conversion-preview/',{'project':self.project.pk,'module':self.module.pk,'source_ids':[self.source.pk]},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(len(r.data['entries']),2)
        import time
        with patch('django.core.signing.time.time',return_value=time.time()+1900):
            expired=self.client.post(BASE+'conversion-apply/',{'preview_token':r.data['preview_token']},format='json')
        self.assertEqual(expired.status_code,400)
        self.assertFalse(UiTestCase.objects.exists())

    def test_model_receives_actual_local_skill_and_contract(self):
        from types import SimpleNamespace
        from ui_automation.case_conversion import convert_with_skill
        from unittest.mock import Mock
        model=SimpleNamespace(pk=3,name='active-model')
        llm=Mock();llm.invoke.return_value=SimpleNamespace(content='{"cases": []}')
        with patch('langgraph_integration.models.LLMConfig.objects.filter') as config, patch('langgraph_integration.views.create_llm_instance',return_value=llm):
            config.return_value.first.return_value=model
            result,info=convert_with_skill([{'id':self.source.pk,'name':'源用例'}],local_skill())
        prompt=llm.invoke.call_args.args[0]
        self.assertIn('读取源用例，保存待采集草稿',prompt)
        self.assertIn('不得编造定位器',prompt)
        self.assertIn('源用例',prompt)
        self.assertEqual(info['id'],3)
