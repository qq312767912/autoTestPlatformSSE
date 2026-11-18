import copy
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
from xml.sax.saxutils import escape
from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError
from projects.models import Project, ProjectMember
from .models import ApiTestCase, ApiTestCaseGroup
from .excel_import import build_plan, read_tables, persist_plan, prepare_migration_variables, json_path
from .views import ApiTestCaseViewSet

def tables():
    return {
        'AutomationTestCase':[{'id':1,'caseName':'scene A','_row':2}],
        'AutomationCaseApi':[
            {'id':12,'automationTestCase':1,'name':'second','order':2,'requestType':'GET','apiAddress':'/second','httpType':'HTTP','host':9,'requestParameterType':'form-data','examineType':"['datasource_check']", '_row':2},
            {'id':11,'automationTestCase':1,'name':'first','order':1,'requestType':'POST','apiAddress':'/first','httpType':'HTTP','host':9,'requestParameterType':'form-data','httpCode':200,'_row':3}],
        'GlobalHost':[{'id':9,'ip':'example.com','_row':2}],
        'AutomationHead':[{'id':1,'automationCaseApi':11,'name':'X-Token','value':'{{token-name}}','_row':2}],
        'AutomationParameter':[{'id':2,'automationCaseApi':12,'name':'key','value':'<relation[db][sheet]>[key]','_row':2}],
        'AutomationJsonAssertData':[{'id':3,'automationCaseApi':12,'jsonAssertPath':'$.code','jsonAssertValue':'ACK','_row':2}],
        'AutomationTestResult':[{'header':'OLD-SECRET-HISTORY'}],
    }

def plan(data=None):
    with patch('api_testcases.excel_import.read_tables',return_value=(data or tables(),'digest')):
        return build_plan(SimpleUploadedFile('old.xlsx',b'file'))

# OOXML fixtures are assembled as minimal XML rather than authored workbooks.
def workbook(sheets):
    output=io.BytesIO()
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        archive.writestr('xl/workbook.xml','<workbook xmlns="'+ns+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join('<sheet name="'+name+'" sheetId="'+str(i)+'" r:id="rId'+str(i)+'"/>' for i,name in enumerate(sheets,1))+'</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join('<Relationship Id="rId'+str(i)+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet'+str(i)+'.xml"/>' for i in range(1,len(sheets)+1))+'</Relationships>')
        for i,rows in enumerate(sheets.values(),1):
            body=''
            for rownum,row in enumerate(rows,1):
                cells=''
                for col,value in enumerate(row):
                    address=chr(65+col)+str(rownum)
                    if str(value).startswith('='):
                        cells+='<c r="'+address+'"><f>'+escape(str(value)[1:])+'</f></c>'
                    else:
                        cells+='<c r="'+address+'" t="inlineStr"><is><t>'+escape(str(value))+'</t></is></c>'
                body+='<row r="'+str(rownum)+'">'+cells+'</row>'
            archive.writestr('xl/worksheets/sheet'+str(i)+'.xml','<worksheet xmlns="'+ns+'"><sheetData>'+body+'</sheetData></worksheet>')
    return SimpleUploadedFile('fixture.xlsx',output.getvalue())

class ExcelMappingTests(SimpleTestCase):
    def test_read_xlsx_and_invalid_files(self):
        result,_=read_tables(workbook({'Sheet':[['id','name'],[1,'接口']]}))
        self.assertEqual(result['Sheet'][0]['name'],'接口')
        for upload in [SimpleUploadedFile('x.csv',b'a,b'),SimpleUploadedFile('x.xlsx',b'not zip')]:
            with self.assertRaises(ValidationError):read_tables(upload)

    def test_formula_rejected(self):
        with self.assertRaisesMessage(ValidationError,'公式'):
            read_tables(workbook({'Sheet':[['id'],['=1+1']]}))

    def test_xls_reader(self):
        sheet=SimpleNamespace(name='Sheet',nrows=2,ncols=2,row_values=lambda i:[['id','name'],[1,'old XLS']][i])
        book=SimpleNamespace(sheets=lambda:[sheet],release_resources=lambda:None)
        with patch('xlrd.open_workbook',return_value=book):
            result,_=read_tables(SimpleUploadedFile('fixture.xls',b'legacy'))
        self.assertEqual(result['Sheet'][0]['name'],'old XLS')

    def test_order_parameters_assertions_and_history_exclusion(self):
        result=plan()
        self.assertFalse(result['errors'])
        self.assertEqual(result['case_count'],1)
        first,second=result['cases'][0]['steps']
        self.assertEqual(first['source_id'],'11')
        self.assertEqual(first['interface_data']['headers']['X-Token'],'${token_name}')
        self.assertEqual(first['interface_data']['body']['type'],'form-data')
        self.assertEqual(second['interface_data']['validators'],[{'eq':['body.code','ACK']}])
        self.assertTrue(second['interface_data']['params']['key'].startswith('${legacy_'))
        self.assertIn('OLD-SECRET-HISTORY',str(result['source_tables']))
        self.assertEqual(result['ignored_history_rows'],0)
        self.assertEqual(result['data_row_count'],1)

    def test_invalid_references_duplicates_and_unsupported_assets(self):
        for mutate in [
            lambda t:t['AutomationCaseApi'][0].update(automationTestCase=999),
            lambda t:t['AutomationCaseApi'][0].update(id=11),
            lambda t:t['AutomationCaseApi'][0].update(order=1),
            lambda t:t['AutomationCaseApi'][0].update(isBinaryApi=True),
            lambda t:t.update(AutomationPythonScript=[{'script':'not run','_row':2}]),
            lambda t:t.update(AutomationRequestPrecondition=[{'active':True,'pythonScript':'raise Exception()','_row':2}]),
            lambda t:t['AutomationJsonAssertData'][0].update(jsonAssertPath='$..code'),
            lambda t:t['AutomationHead'].append({'automationCaseApi':999,'name':'x'}),
        ]:
            data=tables();mutate(data)
            self.assertTrue(plan(data)['errors'])

    def test_raw_json_and_url_path(self):
        data=tables()
        data['AutomationCaseApi'][1]['requestParameterType']='raw'
        data['AutomationParameterRaw']=[{'automationCaseApi':11,'raw':'{"name":"{{account}}"}','_row':2}]
        result=plan(data)
        self.assertFalse(result['errors'])
        self.assertEqual(result['cases'][0]['steps'][0]['interface_data']['body']['content'],{'name':'${account}'})
        self.assertEqual(json_path("$['data'][0].code"),'body.data[0].code')

    def test_missing_external_values_block_and_aliases_work(self):
        result=plan()
        case=result['cases'][0]
        fake=SimpleNamespace(config=case['config'],steps=SimpleNamespace(all=lambda:[SimpleNamespace(interface_data=s['interface_data']) for s in case['steps']]))
        with self.assertRaisesMessage(ValueError,'缺少变量'):prepare_migration_variables(fake,{})
        values={name:'value' for name in result['cases'][0]['config']['excel_migration']['variable_sources']}
        values.pop('token_name')
        values['token-name']='my-token'
        self.assertEqual(prepare_migration_variables(fake,values)['token_name'],'my-token')
        # Explicit null/empty values can be intentional negative-test data.
        self.assertEqual(prepare_migration_variables(fake,{name:None for name in result['cases'][0]['config']['excel_migration']['variable_sources']})['token_name'],None)
        # Users can replace references with concrete values without editing migration metadata.
        fake.steps=SimpleNamespace(all=lambda:[SimpleNamespace(interface_data={'url':'/done'})])
        self.assertEqual(prepare_migration_variables(fake,{}),{})

class ExcelPersistenceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='excel',password='test')
        self.project=Project.objects.create(name='excel',creator=self.user)
        self.view=ApiTestCaseViewSet.as_view({'post':'import_excel'})
    def call(self,extra=None,user=None):
        request=APIRequestFactory().post('/',{'file':SimpleUploadedFile('old.xlsx',b'file'),**(extra or {})},format='multipart')
        force_authenticate(request,user or self.user)
        with patch('api_testcases.excel_import.read_tables',return_value=(tables(),'digest')):
            return self.view(request,project_pk=self.project.pk)

    def test_preview_is_readonly_confirm_persists_steps(self):
        response=self.call()
        self.assertEqual(response.status_code,200,response.data)
        self.assertFalse(ApiTestCase.objects.exists())
        self.assertNotIn('headers',response.data['cases'][0]['steps'][0])
        response=self.call({'confirm':'true','sha256':'digest'})
        self.assertEqual(response.status_code,201,response.data)
        from .models import ApiInterfaceCase
        case=ApiInterfaceCase.objects.get()
        self.assertFalse(ApiTestCase.objects.exists())
        self.assertEqual(list(case.steps.values_list('name',flat=True)),['first','second'])
        self.assertEqual(case.project,self.project)
        self.assertEqual(case.created_by,self.user)

    def test_repeat_name_and_changed_file_are_not_written(self):
        self.assertEqual(self.call({'confirm':'true','sha256':'bad'}).status_code,400)
        self.assertFalse(ApiTestCase.objects.exists())
        self.assertEqual(self.call({'confirm':'true','sha256':'digest'}).status_code,201)
        self.assertEqual(self.call({'confirm':'true','sha256':'digest'}).status_code,400)
        from .models import ApiInterfaceCase
        self.assertEqual(ApiInterfaceCase.objects.count(),1)
        self.assertFalse(ApiTestCase.objects.exists())

    def test_cross_project_group_rejected(self):
        other=Project.objects.create(name='other',creator=self.user)
        group=ApiTestCaseGroup.objects.create(project=other,name='other')
        response=self.call({'confirm':'true','sha256':'digest','group_id':group.pk})
        self.assertEqual(response.status_code,400)
        self.assertFalse(ApiTestCase.objects.exists())

    def test_add_permission_and_project_membership_required(self):
        user=get_user_model().objects.create_user(username='reader')
        ProjectMember.objects.create(project=self.project,user=user,role='member')
        user.user_permissions.add(Permission.objects.get(codename='view_apitestcase'))
        self.assertEqual(self.call(user=user).status_code,403)
        user.user_permissions.add(Permission.objects.get(codename='add_apitestcase'))
        user=get_user_model().objects.get(pk=user.pk)
        ProjectMember.objects.filter(user=user).delete()
        self.assertEqual(self.call(user=user).status_code,403)

    def test_atomic_conflict(self):
        data=tables()
        data['AutomationTestCase'].append({'id':2,'caseName':'scene B','_row':3})
        data['AutomationCaseApi'].append({**data['AutomationCaseApi'][1],'id':13,'automationTestCase':2})
        p=plan(data)
        ApiTestCase.objects.create(project=self.project,name='scene B')
        with self.assertRaises(ValidationError):persist_plan(p,self.project,self.user)
        self.assertFalse(ApiTestCase.objects.filter(name='scene A').exists())

    def test_editable_snapshot_and_migration_guard_survive_update(self):
        from .serializers import ApiTestCaseSerializer
        persist_plan(plan(),self.project,self.user)
        case=ApiTestCase.objects.get()
        result=ApiTestCaseSerializer(case).data
        self.assertGreater(result['steps'][0]['interface_info']['id'],0)
        self.assertEqual(result['steps'][0]['interface_info']['method'],'POST')
        serializer=ApiTestCaseSerializer(case,data={'config':{'variables':{}}},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        serializer.save()
        case.refresh_from_db()
        self.assertIn('excel_migration',case.config)
        with self.assertRaisesMessage(ValueError,'缺少变量'):prepare_migration_variables(case,{})

    def test_runner_blocks_missing_values_before_network(self):
        from .runner import TestCaseRunner
        persist_plan(plan(),self.project,self.user)
        from .services import TestExecutionService
        case=ApiTestCase.objects.get()
        case.config=TestExecutionService._prepare_config(case.config,{})
        self.assertIn('excel_migration',case.config)
        runner=TestCaseRunner(case)
        with patch.object(runner,'test_start') as start:
            with self.assertRaisesMessage(ValueError,'缺少变量'):runner.run_testcase()
            start.assert_not_called()


class ExcelFullDataTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='full-excel',password='test')
        self.project=Project.objects.create(name='full-excel',creator=self.user)

    def historical_plan(self):
        data=tables()
        data['GlobalHost'][0]['name']='原内网环境'
        data['AutomationTestResult']=[
            {'automationCaseApi':11,'header':"{'X-Token':'old-token'}",'parameter':'{}','testTime':'2025-01-01','_row':2},
            {'automationCaseApi':12,'parameter':"{'key':'recorded-input'}",'examineType':"['datasource_check']",
             'examineData':{'datasource_check':[('$.code', {'jsonAssertValue':'ACK','jsonPathResult':'WRONG-ACTUAL'})]},
             'responseData':"{'code':'WRONG-ACTUAL'}",'testTime':'2025-01-02','_row':3}]
        return plan(data)

    def test_recovery_environment_archive_and_expected_are_preserved(self):
        from api_environments.models import ApiEnvironment, ApiEnvironmentVariable
        from .models import ApiExcelImportArchive
        from .excel_data import unseal
        p=self.historical_plan()
        self.assertFalse(p['required_variables'])
        self.assertEqual(p['recovered_variable_count'],2)
        result=persist_plan(p,self.project,self.user)
        self.assertEqual(result['created_environments'],1)
        self.assertEqual(result['created_variables'],2)
        env=ApiEnvironment.objects.get()
        self.assertEqual(env.name,'原内网环境')
        self.assertEqual(env.base_url,'http://example.com')
        self.assertTrue(ApiEnvironmentVariable.objects.get(name='token_name').is_sensitive)
        case=ApiTestCase.objects.get()
        self.assertIn('recorded-input',case.config['variables'].values())
        archive=ApiExcelImportArchive.objects.get()
        self.assertNotIn('old-token',archive.encrypted_payload)
        self.assertEqual(unseal(archive.encrypted_payload)['AutomationTestResult'][0]['header'],"{'X-Token':'old-token'}")
        self.assertEqual(case.config['excel_migration']['archive_id'],archive.pk)

    def test_expected_recovery_uses_expected_configuration_not_response(self):
        data=tables()
        data['AutomationJsonAssertData'][0]['jsonAssertValue']='<relation[db][checks]>[code]'
        data['AutomationTestResult']=[{'automationCaseApi':12,'parameter':"{'key':'input'}",
            'examineData':{'datasource_check':[('$.code', {'jsonAssertValue':'ACK','jsonPathResult':'BAD'})]},
            'responseData':"{'code':'BAD'}",'testTime':'2025','_row':2}]
        p=plan(data)
        values=p['cases'][0]['config']['variables']
        self.assertIn('ACK',values.values())
        self.assertNotIn('BAD',values.values())
        self.assertEqual(len(p['required_variables']),1)  # token genuinely absent
        data['AutomationTestResult'][0].pop('examineData')
        self.assertNotIn('BAD',plan(data)['cases'][0]['config']['variables'].values())

    def test_latest_input_and_user_edits_survive_backfill(self):
        from api_environments.models import ApiEnvironmentVariable
        from .models import ApiExcelImportArchive
        p=self.historical_plan()
        persist_plan(p,self.project,self.user)
        case=ApiTestCase.objects.get()
        case.config['variables']['token_name']='user-token'
        case.save()
        step=case.steps.first();step.interface_data['url']='/user-edit';step.save()
        envvar=ApiEnvironmentVariable.objects.get(name='token_name');envvar.value='"user-env-token"';envvar.save()
        result=persist_plan(p,self.project,self.user,update_existing=True)
        self.assertEqual(result['updated_cases'],1)
        self.assertEqual(result['created_cases'],0)
        case.refresh_from_db();step.refresh_from_db();envvar.refresh_from_db()
        self.assertEqual(case.config['variables']['token_name'],'user-token')
        self.assertEqual(step.interface_data['url'],'/user-edit')
        self.assertEqual(envvar.get_typed_value(),'user-env-token')
        self.assertEqual(ApiExcelImportArchive.objects.count(),1)

    def test_source_api_project_scope_and_pagination(self):
        from .views import ApiTestCaseViewSet
        persist_plan(self.historical_plan(),self.project,self.user)
        case=ApiTestCase.objects.get()
        request=APIRequestFactory().get('/',{'sheet':'AutomationTestResult'})
        force_authenticate(request,self.user)
        response=ApiTestCaseViewSet.as_view({'get':'excel_source'})(request,project_pk=self.project.pk,pk=case.pk)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['total'],2)
        other=Project.objects.create(name='other-source',creator=self.user)
        request=APIRequestFactory().get('/')
        force_authenticate(request,self.user)
        response=ApiTestCaseViewSet.as_view({'get':'excel_source'})(request,project_pk=other.pk,pk=case.pk)
        self.assertEqual(response.status_code,404)

    def test_environment_name_conflict_does_not_overwrite(self):
        from api_environments.models import ApiEnvironment
        ApiEnvironment.objects.create(project=self.project,name='原内网环境',base_url='https://user.example')
        result=persist_plan(self.historical_plan(),self.project,self.user)
        self.assertEqual(result['created_environments'],1)
        self.assertTrue(ApiEnvironment.objects.filter(name='原内网环境',base_url='https://user.example').exists())

    def test_latest_recorded_input_is_selected(self):
        data=tables()
        data['AutomationTestResult']=[
            {'automationCaseApi':11,'header':"{'X-Token':'older'}",'testTime':'2024-01-01'},
            {'automationCaseApi':11,'header':"{'X-Token':'latest'}",'testTime':'2026-01-01'}]
        self.assertEqual(plan(data)['cases'][0]['config']['variables']['token_name'],'latest')

    def test_full_import_needs_environment_permissions(self):
        user=get_user_model().objects.create_user(username='case-only-import')
        ProjectMember.objects.create(project=self.project,user=user,role='member')
        user.user_permissions.add(Permission.objects.get(codename='add_apitestcase'),
            Permission.objects.get(codename='add_apiinterface'),Permission.objects.get(codename='add_apiinterfacecase'))
        request=APIRequestFactory().post('/',{'file':SimpleUploadedFile('old.xlsx',b'file'),'confirm':'true','sha256':'digest'},format='multipart')
        force_authenticate(request,user)
        with patch('api_testcases.excel_import.read_tables',return_value=(tables(),'digest')):
            response=ApiTestCaseViewSet.as_view({'post':'import_excel'})(request,project_pk=self.project.pk)
        self.assertEqual(response.status_code,403)
        self.assertFalse(ApiTestCase.objects.exists())

class ExcelNativeAssetTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='native-excel',password='test')
        self.project=Project.objects.create(name='native-excel',creator=self.user)

    def test_three_asset_layers_and_idempotence(self):
        from api_interfaces.models import ApiInterface
        from .models import ApiInterfaceCase
        p=plan()
        result=persist_plan(p,self.project,self.user)
        self.assertEqual(result['created_interfaces'],2)
        self.assertEqual(result['created_interface_cases'],2)
        case=ApiTestCase.objects.get()
        for step in case.steps.all():
            self.assertIsNotNone(step.origin_interface_id)
            mapping=case.config['excel_migration']['assets'][str(step.config['excel_source_id'])]
            self.assertEqual(mapping['interface_id'],step.origin_interface_id)
            native=ApiInterfaceCase.objects.get(pk=mapping['interface_case_id'])
            self.assertEqual(native.interface_id,step.origin_interface_id)
            self.assertEqual(native.steps.count(),1)
            self.assertEqual(native.steps.get().role,'main')
            self.assertEqual(native.steps.get().interface_data,step.interface_data)
        again=persist_plan(p,self.project,self.user,update_existing=True)
        self.assertEqual(again['created_interfaces'],0)
        self.assertEqual(again['created_interface_cases'],0)
        self.assertEqual(ApiInterface.objects.count(),2)
        self.assertEqual(ApiInterfaceCase.objects.count(),2)

    def test_backfill_uses_current_snapshot_and_keeps_native_edits(self):
        from .models import ApiInterfaceCase
        from .excel_assets import ensure_assets
        p=plan()
        # Simulate an earlier import with no native assets.
        with patch('api_testcases.excel_assets.ensure_assets',return_value={}):
            persist_plan(p,self.project,self.user)
        case=ApiTestCase.objects.get()
        step=case.steps.first()
        step.interface_data['url']='/edited-by-user'
        step.save()
        ensure_assets(case,self.user)
        step.refresh_from_db()
        self.assertEqual(step.origin_interface.url,'/edited-by-user')
        native=ApiInterfaceCase.objects.get(interface=step.origin_interface)
        native.name='user renamed';native.save()
        main=native.steps.get();main.interface_data['url']='/native-edit';main.save()
        persist_plan(p,self.project,self.user,update_existing=True)
        native.refresh_from_db();main.refresh_from_db();step.refresh_from_db()
        self.assertEqual(native.name,'user renamed')
        self.assertEqual(main.interface_data['url'],'/native-edit')
        self.assertEqual(step.interface_data['url'],'/edited-by-user')

    def test_name_collision_preserves_unrelated_case(self):
        from api_interfaces.models import ApiInterface
        from .models import ApiInterfaceCase
        interface=ApiInterface.objects.create(project=self.project,name='original',method='GET',url='/original')
        original=ApiInterfaceCase.objects.create(project=self.project,interface=interface,name='first')
        persist_plan(plan(),self.project,self.user)
        original.refresh_from_db()
        self.assertEqual(original.interface.url,'/original')
        self.assertEqual(original.name,'first')
        self.assertEqual(ApiInterfaceCase.objects.count(),3)

    def test_native_case_edit_preserves_lineage(self):
        from .models import ApiInterfaceCase
        from .serializers import ApiInterfaceCaseSerializer
        persist_plan(plan(),self.project,self.user)
        native=ApiInterfaceCase.objects.first()
        source_id=native.config['excel_migration']['source_api_id']
        serializer=ApiInterfaceCaseSerializer(native,data={'config':{'variables':{'user':'value'}}},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        serializer.save();native.refresh_from_db()
        self.assertEqual(native.config['excel_migration']['source_api_id'],source_id)
        result=persist_plan(plan(),self.project,self.user,update_existing=True)
        self.assertEqual(result['created_interface_cases'],0)
        native.refresh_from_db()
        self.assertEqual(native.config['variables'],{'user':'value'})

class ExcelInterfaceDestinationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser(username='interface-destination',password='test')
        self.project=Project.objects.create(name='destination',creator=self.user)

    def test_new_import_preserves_case_boundary_without_scenarios(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        from api_interfaces.models import ApiInterface
        result=persist_interface_plan(plan(),self.project,self.user)
        self.assertEqual(result['created_interface_cases'],1)
        self.assertEqual(ApiTestCase.objects.count(),0)
        self.assertEqual(ApiInterface.objects.count(),2)
        native=ApiInterfaceCase.objects.get()
        self.assertEqual(native.name,'scene A')
        self.assertEqual(list(native.steps.values_list('name',flat=True)),['first','second'])
        self.assertEqual(list(native.steps.values_list('role',flat=True)),['precondition','main'])
        self.assertEqual(native.interface_id,native.steps.last().origin_interface_id)

    def test_conversion_retires_old_records_preserves_data_and_stops_old_execution(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        from .services import TestExecutionService
        p=plan()
        persist_plan(p,self.project,self.user)
        old_scene=ApiTestCase.objects.get()
        before=list(old_scene.steps.values_list('interface_data',flat=True))
        old_scene.description='user description';old_scene.save()
        result=persist_interface_plan(p,self.project,self.user,update_existing=True)
        self.assertEqual(result['converted_scenarios'],1)
        self.assertEqual(result['consolidated_split_cases'],2)
        grouped=ApiInterfaceCase.objects.get(pk=result['ids'][0])
        self.assertEqual(grouped.description,'user description')
        self.assertEqual(list(grouped.steps.values_list('interface_data',flat=True)),before)
        self.assertEqual(ApiTestCase.objects.count(),1)  # historical source retained
        old_scene.refresh_from_db()
        with self.assertRaisesMessage(ValueError,'已合并到接口用例'):TestExecutionService._prepare_config(old_scene.config)
        request=APIRequestFactory().get('/')
        force_authenticate(request,self.user)
        view=ApiTestCaseViewSet.as_view({'get':'list'})
        response=view(request,project_pk=self.project.pk)
        self.assertEqual(response.data['count'],0)
        request=APIRequestFactory().get('/')
        force_authenticate(request,self.user)
        from .views import ApiInterfaceCaseViewSet
        response=ApiInterfaceCaseViewSet.as_view({'get':'list'})(request,project_pk=self.project.pk)
        self.assertEqual(response.data['count'],1)
        again=persist_interface_plan(p,self.project,self.user,update_existing=True)
        self.assertEqual(again['created_interface_cases'],0)
        self.assertEqual(again['created_interfaces'],0)

    def test_grouped_edits_are_authoritative_on_reimport(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        p=plan();persist_interface_plan(p,self.project,self.user)
        case=ApiInterfaceCase.objects.get()
        step=case.steps.first();step.interface_data['url']='/custom';step.save()
        case.config['variables']['token_name']='user-token';case.save()
        persist_interface_plan(p,self.project,self.user,update_existing=True)
        step.refresh_from_db();case.refresh_from_db()
        self.assertEqual(step.interface_data['url'],'/custom')
        self.assertEqual(case.config['variables']['token_name'],'user-token')

    def test_first_request_interface_filter_finds_group(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        from .views import ApiInterfaceCaseViewSet
        persist_interface_plan(plan(),self.project,self.user)
        case=ApiInterfaceCase.objects.get()
        first=case.steps.first().origin_interface_id
        self.assertNotEqual(first,case.interface_id)
        request=APIRequestFactory().get('/',{'interface_id':first})
        force_authenticate(request,self.user)
        response=ApiInterfaceCaseViewSet.as_view({'get':'list'})(request,project_pk=self.project.pk)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['count'],1)

    def test_legacy_url_also_imports_only_interface_cases(self):
        from .models import ApiInterfaceCase
        request=APIRequestFactory().post('/',{'file':SimpleUploadedFile('old.xlsx',b'file'),'confirm':'true','sha256':'digest'},format='multipart')
        force_authenticate(request,self.user)
        with patch('api_testcases.excel_import.read_tables',return_value=(tables(),'digest')):
            response=ApiTestCaseViewSet.as_view({'post':'import_excel'})(request,project_pk=self.project.pk)
        self.assertEqual(response.status_code,201,response.data)
        self.assertFalse(ApiTestCase.objects.exists())
        self.assertEqual(ApiInterfaceCase.objects.count(),1)

    def test_conversion_preserves_edited_split_request(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        p=plan();persist_plan(p,self.project,self.user)
        split=ApiInterfaceCase.objects.get(config__excel_migration__source_api_id='11')
        step=split.steps.get();step.interface_data['url']='/split-user-edit';step.save()
        result=persist_interface_plan(p,self.project,self.user,update_existing=True)
        grouped=ApiInterfaceCase.objects.get(pk=result['ids'][0])
        self.assertEqual(grouped.steps.first().interface_data['url'],'/split-user-edit')
        self.assertEqual(ApiTestCase.objects.get().steps.first().interface_data['url'],'/first')

    def test_conflicting_edits_fail_atomically(self):
        from .excel_interface_import import persist_interface_plan
        from .models import ApiInterfaceCase
        p=plan();persist_plan(p,self.project,self.user)
        scene=ApiTestCase.objects.get()
        a=scene.steps.first();a.interface_data['url']='/scene-edit';a.save()
        split=ApiInterfaceCase.objects.get(config__excel_migration__source_api_id='11')
        b=split.steps.get();b.interface_data['url']='/other-edit';b.save()
        with self.assertRaisesMessage(ValidationError,'均有不同修改'):
            persist_interface_plan(p,self.project,self.user,update_existing=True)
        self.assertEqual(ApiInterfaceCase.objects.count(),2)
        scene.refresh_from_db()
        self.assertNotIn('superseded_by_interface_case',scene.config['excel_migration'])
