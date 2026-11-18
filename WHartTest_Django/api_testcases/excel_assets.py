"""Excel 请求补齐原生接口与接口用例；始终以当前场景快照为创建来源，不覆盖已编辑资产。"""
import copy
import json
import re
from rest_framework.exceptions import ValidationError
from api_interfaces.models import ApiInterface
from .models import ApiInterfaceCase, ApiInterfaceCaseStep

def ensure_assets(case,user):
    migration=copy.deepcopy((case.config or {}).get('excel_migration') or {})
    mappings=migration.setdefault('assets',{})
    counts={'created_interfaces':0,'created_interface_cases':0,'linked_steps':0}
    for step in case.steps.all().order_by('order'):
        source_id=str((step.config or {}).get('excel_source_id') or '')
        if not source_id:continue
        data=copy.deepcopy(step.interface_data or {})
        mapping=mappings.get(source_id,{})
        native=ApiInterfaceCase.objects.filter(project=case.project,
            config__excel_migration__sha256=migration['sha256'],
            config__excel_migration__source_api_id=source_id).order_by('pk').first()
        interface=step.origin_interface
        if interface and interface.project_id!=case.project_id:
            raise ValidationError('场景步骤关联了其他项目的接口')
        if not interface and native:interface=native.interface
        if not interface and mapping.get('interface_id'):
            interface=ApiInterface.objects.filter(project=case.project,pk=mapping['interface_id']).first()
        references=set(re.findall(r'\$\{([A-Za-z_]\w*)\}',json.dumps(data,ensure_ascii=False)))
        variables={name:value for name,value in (case.config.get('variables') or {}).items() if name in references}
        if not interface:
            fields={key:data.get(key,default) for key,default in [
                ('headers',{}),('params',{}),('body',{}),('path_params',[]),('validators',[]),('extract',{}),
                ('setup_hooks',[]),('teardown_hooks',[]),('extract_meta',{}),('file_ids',step.file_ids or [])]}
            interface=ApiInterface.objects.create(project=case.project,created_by=user,name=step.name,
                type='http',method=data.get('method'),url=data.get('url'),variables=variables,**fields)
            counts['created_interfaces']+=1
        if native and native.interface_id!=interface.pk:
            raise ValidationError('已有接口用例关联与场景步骤不一致，请先核对关联后补全')
        if not native:
            name=step.name
            suffix=0
            while ApiInterfaceCase.objects.filter(project=case.project,name=name).exists():
                suffix+=1
                tag=' · Excel '+source_id+(('-'+str(suffix)) if suffix>1 else '')
                name=step.name[:100-len(tag)]+tag
            config=copy.deepcopy(case.config)
            config['variables']=variables
            config['excel_migration']={key:value for key,value in migration.items() if key!='assets'}
            config['excel_migration']['source_case_id']=migration.get('source_id')
            config['excel_migration']['source_api_id']=source_id
            config['excel_migration']['variable_sources']={name:value for name,value in migration.get('variable_sources',{}).items() if name in references}
            native=ApiInterfaceCase.objects.create(project=case.project,interface=interface,created_by=user,
                name=name,priority=case.priority,group=case.group,config=config)
            ApiInterfaceCaseStep.objects.create(interface_case=native,origin_interface=interface,name=step.name,
                role=ApiInterfaceCaseStep.ROLE_MAIN,order=1,interface_data=data,file_ids=step.file_ids or [],config={'excel_source_id':source_id})
            counts['created_interface_cases']+=1
        # Existing data, order, assertions and explicit synchronization settings are untouched.
        if step.origin_interface_id!=interface.pk:
            step.origin_interface=interface
            step.save(update_fields=['origin_interface'])
            counts['linked_steps']+=1
        mappings[source_id]={'interface_id':interface.pk,'interface_case_id':native.pk}
    case.config={**case.config,'excel_migration':migration}
    case.save(update_fields=['config','updated_at'])
    return counts
