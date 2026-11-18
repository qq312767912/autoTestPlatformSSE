"""旧平台数据恢复：请求输入可复用；预期仅来自历史配置的 jsonAssertValue，绝不取实际响应。"""
import base64
import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from django.conf import settings

HISTORY=('AutomationTestResult','AutomationCaseTestResult','ManyTimesAutomationTestResult')

def seal(tables):
    key=base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+':api-excel-archive').encode()).digest())
    return Fernet(key).encrypt(json.dumps(tables,ensure_ascii=False,default=str).encode()).decode()

def unseal(payload):
    key=base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+':api-excel-archive').encode()).digest())
    return json.loads(Fernet(key).decrypt(payload.encode()))

def enrich_plan(plan,tables,parse_value,json_path):
    history=defaultdict(list)
    for sheet in HISTORY:
        for row in tables.get(sheet,[]):
            aid=str(row.get('automationCaseApi','')).removesuffix('.0')
            stamp=str(row.get('testTime') or row.get('apiTestTime') or '')
            try:order=int(float(row.get('id') or 0))
            except (ValueError,TypeError):order=0
            history[aid].append((stamp,order,sheet,row))
    for rows in history.values():rows.sort(key=lambda x:x[:2],reverse=True)
    resolved=set()
    for case in plan['cases']:
        defaults={};provenance={};ranks={}
        def remember(value,source,record):
            if not isinstance(source,str):return
            match=re.fullmatch(r'\$\{([A-Za-z_]\w*)\}',source)
            if not match:return
            name=match[1];rank=record[:2]
            if name in ranks and ranks[name]>=rank:return
            defaults[name]=value;ranks[name]=rank
            provenance[name]={'sheet':record[2],'row':record[3].get('_row',0),'time':record[0],'kind':'recorded_input'}
        for step in case['steps']:
            request=step['interface_data']
            candidates=history.get(step['source_id'],[])
            header_fields=request.get('headers',{})
            body=request.get('body') or {}
            fields=dict(request.get('params') or {})
            if isinstance(body.get('content'),list):
                fields.update({item['key']:item.get('value') for item in body['content'] if isinstance(item,dict) and item.get('key')})
            elif isinstance(body.get('content'),dict):fields.update(body['content'])
            for record in candidates:
                row=record[3]
                headers=parse_value(row.get('header') or '{}')
                params=parse_value(row.get('parameter') or '{}')
                if isinstance(headers,dict):
                    for key,reference in header_fields.items():
                        if key in headers:remember(headers[key],reference,record)
                if isinstance(params,dict):
                    for key,reference in fields.items():
                        if key in params:remember(params[key],reference,record)
                expected={}
                for key in ['examineData','normalCheckData','failCheckData']:
                    checks=parse_value(row.get(key) or '{}')
                    # Failed actual values are irrelevant; explicit expected configuration is retained.
                    if not isinstance(checks,dict):continue
                    for items in checks.values():
                        if not isinstance(items,(list,tuple)):continue
                        for item in items:
                            if not isinstance(item,(list,tuple)) or len(item)!=2 or not isinstance(item[1],dict) or 'jsonAssertValue' not in item[1]:continue
                            try:expected[json_path(item[0])]=item[1]['jsonAssertValue']
                            except (ValueError,TypeError):continue
                for validator in request.get('validators',[]):
                    pair=validator.get('eq',[])
                    if len(pair)>=2 and pair[0] in expected:
                        remember(expected[pair[0]],pair[1],record)
                        match=re.fullmatch(r'\$\{([A-Za-z_]\w*)\}',str(pair[1]))
                        if match and provenance.get(match[1],{}).get('sheet')==record[2] and provenance.get(match[1],{}).get('row')==record[3].get('_row',0):
                            provenance[match[1]]['kind']='recorded_expected_config'
        case['config']['variables']=defaults
        case['config']['excel_migration']['value_provenance']=provenance
        resolved.update(defaults)
    missing=set()
    for case in plan['cases']:
        missing.update(set(case['config']['excel_migration']['variable_sources'])-set(case['config']['variables']))
    plan['required_variables']=[row for row in plan['required_variables'] if row['name'] in missing]
    # Earlier conversion notices are superseded by the exact unresolved list.
    plan['warnings']=[w for w in plan['warnings'] if not w['message'].startswith(('需配置环境变量 ','外部数据源缺少实际值，需配置变量 '))]
    if resolved:plan['warnings'].append({'sheet':'历史请求/校验数据','row':0,'message':'已恢复文件中的历史请求输入和明确的预期配置；令牌等历史值可能过期，导入后可在环境变量中更新。实际响应没有转成断言。'})
    plan['recovered_variable_count']=len(resolved)
    plan['data_row_count']=sum(len(tables.get(name,[])) for name in HISTORY)
    plan['sheet_counts']={name:len(rows) for name,rows in tables.items()}
    plan['source_tables']=tables
    plan['ignored_history_rows']=0
    environments=[]
    for host in tables.get('GlobalHost',[]):
        hid=str(host.get('id','')).removesuffix('.0')
        references=[step for case in plan['cases'] for step in case['steps'] if step.get('source_host_id')==hid]
        base=(references[0]['source_base_url'] if references else str(host.get('host') or host.get('ip') or '').strip())
        if base and '://' not in base:base='http://'+base
        if not base:continue
        if urlparse(base).scheme not in {'http','https'} or not urlparse(base).netloc:
            plan['warnings'].append({'sheet':'GlobalHost','row':host.get('_row',0),'message':'环境地址无效，源数据已保留，未创建不可用环境'})
            continue
        variables={};conflicts=set()
        for case in plan['cases']:
            if not any(s.get('source_host_id')==hid for s in case['steps']):continue
            for name,value in case['config']['variables'].items():
                if name in variables and variables[name]!=value:conflicts.add(name)
                else:variables[name]=value
        environments.append({'source_id':hid,'name':(str(host.get('name') or '').strip() or 'Excel 环境 '+hid)[:100],'base_url':base,
                             'variables':{name:value for name,value in variables.items() if name not in conflicts},
                             'is_active':str(host.get('status','1')).lower() not in {'0','false'},'description':str(host.get('description') or '')})
    plan['environments']=environments;plan['environment_count']=len(environments)
    return plan
