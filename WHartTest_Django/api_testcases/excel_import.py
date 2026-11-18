"""旧接口平台 Excel 迁移；仅解析资产定义，不执行脚本或使用历史结果补值。"""
import ast
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from urllib.parse import urlparse
from django.db import transaction
from rest_framework.exceptions import ValidationError
from .models import ApiTestCase, ApiTestCaseStep, ApiTestCaseGroup

MAX_SIZE=20*1024*1024
HISTORY={'AutomationTestResult','AutomationCaseTestResult','ManyTimesAutomationTestResult'}
SUPPORTED={'AutomationTestCase','AutomationCaseApi','GlobalHost','AutomationHead','AutomationParameter',
           'AutomationParameterRaw','AutomationJsonAssertData','AutomationRequestPrecondition','ExcelRelationParameter'}
SCRIPT_SHEETS={'AutomationPythonScript','AutomationJdbcCheckData','AutomationRelationDatabase',
               'AutomationResponseCheckData','AutomationScriptCheckData','AutomationCustomScript','GlobalVariableScript','AutomationResponseJson'}

def text(v): return '' if v is None else str(v).strip()
def identifier(v):
    if isinstance(v,float) and v.is_integer(): v=int(v)
    return text(v)
def truth(v): return text(v).lower() in {'true','1','yes'}
def read_tables(upload):
    name=text(upload.name).lower()
    if not name.endswith(('.xlsx','.xls')): raise ValidationError('请选择 .xlsx 或 .xls 文件')
    data=upload.read(MAX_SIZE+1)
    if len(data)>MAX_SIZE: raise ValidationError('Excel 文件不能超过20MB')
    tables={}
    try:
        if name.endswith('.xlsx'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(x.file_size for x in archive.infolist())>100*1024*1024: raise ValidationError('Excel 解压后超过100MB')
            from openpyxl import load_workbook
            book=load_workbook(io.BytesIO(data),read_only=True,data_only=False)
            try:
                for sheet in book:
                    rows=[]
                    for i,cells in enumerate(sheet.iter_rows()):
                        row=[cell.value for cell in cells]
                        if i>50000 or len(row)>100: raise ValidationError('工作表超过50000行或100列')
                        if any(cell.data_type=='f' for cell in cells):
                            raise ValidationError(f'工作表 {sheet.title} 包含公式，请转换为值后导入')
                        rows.append(list(row))
                    tables[sheet.title]=rows
            finally: book.close()
        else:
            import xlrd
            book=xlrd.open_workbook(file_contents=data)
            try:
                for sheet in book.sheets():
                    if sheet.nrows>50001 or sheet.ncols>100: raise ValidationError('工作表超过50000行或100列')
                    tables[sheet.name]=[sheet.row_values(i) for i in range(sheet.nrows)]
            finally: book.release_resources()
    except ValidationError: raise
    except Exception: raise ValidationError('Excel 文件损坏、加密或格式不受支持')
    result={}
    for name,rows in tables.items():
        if not rows: result[name]=[];continue
        headers=[text(v) for v in rows[0]]
        if len(headers)!=len(set(headers)): raise ValidationError(f'工作表 {name} 的列名重复')
        result[name]=[{**dict(zip(headers,row)),'_row':i+2} for i,row in enumerate(rows[1:]) if any(text(v) for v in row)]
    return result,hashlib.sha256(data).hexdigest()

def json_path(value):
    value=text(value)
    if value=='$': return 'body'
    if re.fullmatch(r'\$(?:\.[A-Za-z_]\w*|\[\d+\]|\[[\x27"][A-Za-z_]\w*[\x27"]\])+',value):
        value=re.sub(r'\[[\x27"]([A-Za-z_]\w*)[\x27"]\]',r'.\1',value)
        return 'body'+value[1:]
    raise ValueError('仅支持明确字段及数组下标的 JSONPath，请调整复杂路径')

def parse_value(value):
    if not isinstance(value,str): return value
    try:return json.loads(value)
    except (ValueError,TypeError):
        try:return ast.literal_eval(value)
        except (ValueError,SyntaxError):return value

def build_plan(upload):
    tables,digest=read_tables(upload)
    for name,columns in {'AutomationTestCase':{'id','caseName'},'AutomationCaseApi':{'id','name','requestType','apiAddress','automationTestCase'}}.items():
        if name not in tables or not tables[name] or not columns.issubset(tables[name][0]):
            raise ValidationError('未识别旧平台导出结构：缺少 '+name+' 或必要列')
    errors=[];warnings=[];required={};aliases={}
    def warn(sheet,row,message): warnings.append({'sheet':sheet,'row':row.get('_row',0),'message':message})
    def fail(sheet,row,message): errors.append({'sheet':sheet,'row':row.get('_row',0),'message':message})
    def convert(value,sheet,row):
        if not isinstance(value,str):return value
        def variable(original,external=False):
            safe=re.sub(r'[^A-Za-z0-9_]','_',original)
            if not safe or not re.match(r'[A-Za-z_]',safe): safe='legacy_'+safe
            if external: safe='legacy_'+hashlib.sha256(original.encode()).hexdigest()[:12]
            if safe in required and required[safe]!=original:
                safe+='_'+hashlib.sha256(original.encode()).hexdigest()[:8]
            required[safe]=original
            if not external:aliases[safe]=original
            warn(sheet,row,('外部数据源缺少实际值，需配置变量 ' if external else '需配置环境变量 ')+safe+'（'+original+'）')
            return '${'+safe+'}'
        value=re.sub(r'<relation\[[^\n]+?\]>\[[^\]]+\]',lambda m:variable(m.group(),True),value)
        return re.sub(r'\{\{\s*([^{}]+?)\s*\}\}',lambda m:variable(m.group(1)),value)
    for name,rows in tables.items():
        if rows and name not in SUPPORTED|HISTORY:
            for row in rows: fail(name,row,'该工作表的资产尚无可靠映射，请移除或先迁移后再导入')
    pre=tables.get('AutomationRequestPrecondition',[])
    for row in pre:
        if truth(row.get('active')) and text(row.get('pythonScript')):
            fail('AutomationRequestPrecondition',row,'启用的旧平台 Python 前置脚本需人工迁移，导入不会执行脚本')
        elif text(row.get('pythonScript')):
            warn('AutomationRequestPrecondition',row,'未启用的前置脚本不迁移；原脚本仍保留在源 Excel 中')
    hosts={identifier(row.get('id')):row for row in tables.get('GlobalHost',[])}
    case_rows=tables['AutomationTestCase'];case_ids=set();api_ids=set()
    related={}
    for sheet in ['AutomationHead','AutomationParameter','AutomationJsonAssertData','AutomationParameterRaw']:
        by_api=defaultdict(list)
        for row in tables.get(sheet,[]):by_api[identifier(row.get('automationCaseApi'))].append(row)
        related[sheet]=by_api
    cases=[]
    for row in case_rows:
        cid=identifier(row.get('id'));name=text(row.get('caseName'))
        if not cid or cid in case_ids or not name or len(name)>100:
            fail('AutomationTestCase',row,'用例ID重复/为空，或名称为空/超过100字');continue
        case_ids.add(cid)
        cases.append({'source_id':cid,'name':name,'description':text(row.get('description')),'steps':[]})
    names=[case['name'] for case in cases]
    if len(names)!=len(set(names)):errors.append({'sheet':'AutomationTestCase','row':0,'message':'Excel 内有同名用例，请先重命名'})
    by_case={case['source_id']:case for case in cases}
    for row in tables['AutomationCaseApi']:
        aid=identifier(row.get('id'));cid=identifier(row.get('automationTestCase'))
        if not aid or aid in api_ids or cid not in by_case:
            fail('AutomationCaseApi',row,'步骤ID重复/为空或引用了不存在的用例');continue
        api_ids.add(aid);case=by_case[cid]
        if truth(row.get('isBinaryApi')):
            fail('AutomationCaseApi',row,'二进制或文件请求需人工迁移附件');continue
        if text(row.get('httpType')).upper() not in {'','HTTP','HTTPS'}:
            fail('AutomationCaseApi',row,'仅支持 HTTP/HTTPS 请求');continue
        method=text(row.get('requestType')).upper()
        url=text(row.get('apiAddress'));name=text(row.get('name'))
        if method not in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} or not url or not name or len(name)>100:
            fail('AutomationCaseApi',row,'请求方法、地址或名称无效');continue
        host=hosts.get(identifier(row.get('host')),{})
        base=text(host.get('host')) or text(host.get('ip'))
        if base and '://' not in base:base=('https' if text(row.get('httpType')).upper()=='HTTPS' else 'http')+'://'+base
        if base and (urlparse(base).scheme not in {'http','https'} or not urlparse(base).netloc):
            fail('GlobalHost',host,'环境地址无效');continue
        if not base and not url.startswith(('http://','https://')):warn('AutomationCaseApi',row,'无环境地址，执行前需选择目标环境')
        headers={};params={}
        for sheet,target in [('AutomationHead',headers),('AutomationParameter',params)]:
            for child in related[sheet].get(aid,[]):
                key=text(child.get('name'))
                if not key or key in target:fail(sheet,child,'参数名为空或重复，不能无损映射');continue
                target[key]=convert(child.get('value') if child.get('value') is not None else '',sheet,child)
        body={'type':'none','content':None};query={}
        parameter_type=text(row.get('requestParameterType')).lower()
        if method in {'GET','HEAD'}:query=params
        elif parameter_type in {'form-data','x-www-form-urlencoded'}:
            body={'type':parameter_type,'content':[{'key':key,'value':value,'enabled':True,'type':'text'} for key,value in params.items()]}
        elif parameter_type in {'raw','json','application/json'}:
            rawrows=related['AutomationParameterRaw'].get(aid,[])
            if len(rawrows)!=1:fail('AutomationParameterRaw',row,'raw 请求必须有一条明确的原始请求体')
            elif rawrows:
                raw=rawrows[0]
                key=next((key for key in ['raw','data','value','parameterRaw','rawData','requestData'] if key in raw),None)
                if not key:fail('AutomationParameterRaw',raw,'无法识别原始请求体列')
                else:body={'type':'raw','content':parse_value(convert(raw[key],'AutomationParameterRaw',raw))}
        elif params:fail('AutomationCaseApi',row,'不支持的参数类型：'+parameter_type)
        validators=[]
        code=text(row.get('httpCode'))
        if code:
            try:validators.append({'eq':['status_code',int(float(code))]})
            except ValueError:fail('AutomationCaseApi',row,'HTTP状态码格式错误')
        for assertion in related['AutomationJsonAssertData'].get(aid,[]):
            try:
                if truth(assertion.get('pathInterrelate')):raise ValueError('动态断言路径需人工迁移')
                check=json_path(assertion.get('jsonAssertPath'))
                expect=convert(assertion.get('jsonAssertValue'),'AutomationJsonAssertData',assertion)
                validators.append({'eq':[check,expect]})
            except ValueError as exc:fail('AutomationJsonAssertData',assertion,str(exc))
        examine=parse_value(text(row.get('examineType')) or '[]')
        if not isinstance(examine,list) or any(x not in {'datasource_check','httpCode_check','status_code_check'} for x in examine):
            fail('AutomationCaseApi',row,'包含未支持的旧平台校验类型')
        if isinstance(examine,list) and 'datasource_check' in examine and not related['AutomationJsonAssertData'].get(aid):
            fail('AutomationCaseApi',row,'声明了数据源校验，但缺少对应 JSON 断言')
        if not validators:fail('AutomationCaseApi',row,'没有可迁移断言，请先补充明确断言')
        try:order=int(float(row.get('order') or 0))
        except (ValueError,TypeError):order=0;fail('AutomationCaseApi',row,'步骤顺序格式无效')
        case['steps'].append({'source_id':aid,'name':name,'source_order':order,'source_base_url':base,
            'interface_data':{'type':'http','method':method,'url':convert(url,'AutomationCaseApi',row),'headers':headers,'params':query,'body':body,'validators':validators,'extract':{}}})
    for sheet,by_api in related.items():
        for aid,rows in by_api.items():
            if aid not in api_ids:
                for row in rows:fail(sheet,row,'引用了不存在的接口步骤')
    for case in cases:
        case['steps'].sort(key=lambda s:s['source_order'])
        if len({s['source_order'] for s in case['steps']})!=len(case['steps']):errors.append({'sheet':'AutomationCaseApi','row':0,'message':case['name']+' 的步骤顺序重复'})
        if not case['steps']:errors.append({'sheet':'AutomationCaseApi','row':0,'message':case['name']+' 没有接口步骤'})
        bases={s['source_base_url'] for s in case['steps'] if s['source_base_url']}
        base=next(iter(bases)) if len(bases)==1 else ''
        if len(bases)>1:
            for step in case['steps']:
                if step['interface_data']['url'].startswith('/'):step['interface_data']['url']=step['source_base_url'].rstrip('/')+step['interface_data']['url']
            warnings.append({'sheet':'AutomationCaseApi','row':0,'message':case['name']+' 包含多个环境，步骤保留各自完整地址，执行环境不会覆盖完整地址'})
        source=json.dumps([s['interface_data'] for s in case['steps']],ensure_ascii=False)
        case_required={name:value for name,value in required.items() if '${'+name+'}' in source}
        case['config']={'base_url':base,'variables':{},'excel_migration':{'sha256':digest,'source_id':case['source_id'],'variable_sources':case_required,'variable_aliases':{name:value for name,value in aliases.items() if name in case_required}}}
    return {'format':'legacy-api-excel','sha256':digest,'cases':cases,'warnings':warnings,'errors':errors,
            'case_count':len(cases),'step_count':sum(len(c['steps']) for c in cases),
            'required_variables':[{'name':key,'source':value} for key,value in required.items()],
            'ignored_history_rows':sum(len(tables.get(name,[])) for name in HISTORY)}

def persist_plan(plan,project,user,group_id=None):
    if plan['errors']:raise ValidationError({'errors':plan['errors']})
    with transaction.atomic():
        # Serialize imports within a project; never overwrite an existing asset.
        type(project).objects.select_for_update().get(pk=project.pk)
        group=None
        if group_id:
            try:group=ApiTestCaseGroup.objects.get(pk=group_id,project=project)
            except (ApiTestCaseGroup.DoesNotExist,ValueError,TypeError):raise ValidationError('目标分组不属于当前项目')
        names=[c['name'] for c in plan['cases']]
        if len(names)!=len(set(names)):raise ValidationError('Excel 内有同名用例，请先重命名')
        duplicates=list(ApiTestCase.objects.filter(project=project,name__in=names).values_list('name',flat=True))
        if duplicates:raise ValidationError({'message':'已有同名用例，未导入任何记录','duplicates':duplicates})
        ids=[]
        for item in plan['cases']:
            case=ApiTestCase.objects.create(project=project,created_by=user,group=group,name=item['name'],description=item['description'],config=item['config'])
            ApiTestCaseStep.objects.bulk_create([ApiTestCaseStep(testcase=case,name=step['name'],order=i+1,interface_data=step['interface_data'],
                config={'excel_source_id':step['source_id']}) for i,step in enumerate(item['steps'])])
            ids.append(case.pk)
    return {'created_cases':len(ids),'created_steps':plan['step_count'],'ids':ids}


def prepare_migration_variables(testcase, variables):
    """外部数据源未迁移时，在发送任何请求前失败；替换了占位符则无需保留旧映射。"""
    migration=(testcase.config or {}).get('excel_migration') or {}
    if not migration:return variables
    values=dict(variables)
    for name,original in migration.get('variable_aliases',{}).items():
        if name not in values and original in values:values[name]=values[original]
    source=json.dumps([step.interface_data for step in testcase.steps.all()],ensure_ascii=False)
    required=migration.get('variable_sources',{})
    missing=[name for name in required if '${'+name+'}' in source and name not in values]
    if missing:raise ValueError('Excel 导入用例缺少变量，请在执行环境或用例变量中补齐：'+', '.join(missing))
    return values
