"""按旧平台用例边界导入接口用例；历史误建资产保留为迁移来源。"""
import copy
import json
from django.db import transaction
from rest_framework.exceptions import ValidationError
from api_interfaces.models import ApiInterface
from .models import ApiTestCase,ApiInterfaceCase,ApiInterfaceCaseStep
from .excel_import import persist_plan,parse_value

META='excel_migration'
RETIRED='superseded_by_interface_case'

def compatible(a,b):
    return all(a.get(key)==b.get(key) for key in ['type','method','url','headers','params','body','validators','extract'])

def merge_variables(defaults,*sources):
    result=copy.deepcopy(defaults)
    for source in sources:
        values=parse_value(source or {})
        if not isinstance(values,dict):raise ValidationError('已有用例变量格式无效，请先修正')
        for key,value in values.items():
            if key in result and result[key]!=value and result[key]!=defaults.get(key) and value!=defaults.get(key):
                raise ValidationError('迁移来源中的变量分别被编辑且不一致，请先核对变量：'+key)
            if value!=defaults.get(key) or key not in result:result[key]=value
    return result

def preview_existing(plan,project):
    groups=list(ApiInterfaceCase.objects.filter(project=project,config__excel_migration__sha256=plan['sha256'],
                                                config__excel_migration__kind='interface_case'))
    legacy=list(ApiTestCase.objects.filter(project=project,config__excel_migration__sha256=plan['sha256']))
    ids={item['source_id'] for item in plan['cases']}
    updatable=[case.name for case in groups if case.config.get(META,{}).get('source_id') in ids]
    legacy_names=[case.name for case in legacy if case.config.get(META,{}).get('source_id') in ids and not case.config[META].get(RETIRED)]
    owned_ids={case.pk for case in groups if case.config[META].get('source_id') in ids}
    collisions=list(ApiInterfaceCase.objects.filter(project=project,name__in=[item['name'] for item in plan['cases']]).exclude(pk__in=owned_ids).values_list('name',flat=True))
    return {'updatable':updatable+legacy_names,'duplicates':collisions,'legacy_scene_count':len(legacy_names)}

def persist_interface_plan(plan,project,user,group_id=None,update_existing=False):
    if plan['errors']:raise ValidationError({'errors':plan['errors']})
    with transaction.atomic():
        type(project).objects.select_for_update().get(pk=project.pk)
        preview=preview_existing(plan,project)
        if preview['duplicates']:raise ValidationError({'message':'已有其他来源的同名接口用例，未导入','duplicates':preview['duplicates']})
        if preview['updatable'] and not update_existing:raise ValidationError('请勾选补全/迁移已有导入记录')
        # Reuse archive/environment persistence without constructing scenario assets.
        resources=persist_plan({**plan,'cases':[]},project,user,group_id,False)
        from .models import ApiTestCaseGroup
        group=ApiTestCaseGroup.objects.get(pk=group_id,project=project) if group_id else None
        native_ids=[];interface_ids=[];created=0;updated=0;created_interfaces=0;retired_scenes=0;retired_splits=0;created_steps=0
        for item in plan['cases']:
            existing=ApiInterfaceCase.objects.filter(project=project,config__excel_migration__sha256=plan['sha256'],
                config__excel_migration__source_id=item['source_id'],config__excel_migration__kind='interface_case').first()
            legacy=ApiTestCase.objects.filter(project=project,config__excel_migration__sha256=plan['sha256'],
                config__excel_migration__source_id=item['source_id']).order_by('pk').first()
            legacy_steps={str(s.config.get('excel_source_id')):s for s in legacy.steps.all()} if legacy else {}
            splits=list(ApiInterfaceCase.objects.filter(project=project,config__excel_migration__sha256=plan['sha256'],
                config__excel_migration__source_api_id__in=[s['source_id'] for s in item['steps']]))
            split_by_id={str(s.config[META]['source_api_id']):s for s in splits}
            if existing:
                old=copy.deepcopy(existing.config)
                old['variables']=merge_variables(item['config']['variables'],old.get('variables'))
                old[META]={**old.get(META,{}),'archive_id':resources['archive_id'],'environment_ids':resources['environment_ids']}
                existing.config=old;existing.save(update_fields=['config','updated_at'])
                native_ids.append(existing.pk);updated+=1
                interface_ids.extend(existing.steps.exclude(origin_interface__isnull=True).values_list('origin_interface_id',flat=True))
                continue
            prepared=[]
            for source in item['steps']:
                scene_step=legacy_steps.get(source['source_id'])
                split=split_by_id.get(source['source_id'])
                split_main=split.steps.filter(role='main').first() if split else None
                data=copy.deepcopy(scene_step.interface_data if scene_step else source['interface_data'])
                if split_main and not compatible(data,split_main.interface_data):
                    if compatible(data,source['interface_data']):data=copy.deepcopy(split_main.interface_data)
                    elif not compatible(split_main.interface_data,source['interface_data']):
                        raise ValidationError('场景与拆分接口用例均有不同修改，请先核对：'+source['name'])
                interface=scene_step.origin_interface if scene_step else None
                if not interface and split:interface=split.interface
                if existing and not interface:
                    saved=existing.steps.filter(config__excel_source_id=source['source_id']).first()
                    interface=saved.origin_interface if saved else None
                if interface and interface.project_id!=project.pk:raise ValidationError('接口关联跨项目')
                if not interface:
                    fields={key:copy.deepcopy(data.get(key) if data.get(key) is not None else default) for key,default in [
                        ('headers',{}),('params',{}),('body',{}),('validators',[]),('extract',{}),('path_params',[]),
                        ('setup_hooks',[]),('teardown_hooks',[]),('extract_meta',{})]}
                    interface=ApiInterface.objects.create(project=project,created_by=user,name=scene_step.name if scene_step else source['name'],
                        type='http',method=data['method'],url=data['url'],variables=item['config']['variables'],**fields)
                    created_interfaces+=1
                interface_ids.append(interface.pk)
                prepared.append((source,scene_step,interface,data))
            if not prepared:raise ValidationError('接口用例没有请求步骤')
            meta={**copy.deepcopy(item['config'][META]),'kind':'interface_case','archive_id':resources['archive_id'],
                  'environment_ids':resources['environment_ids'],'source_scenario_ids':[legacy.pk] if legacy else [],
                  'source_split_case_ids':[s.pk for s in splits]}
            base=copy.deepcopy(legacy.config if legacy else item['config'])
            base[META]={**base.get(META,{}),**meta}
            base[META].pop(RETIRED,None)
            base['variables']=merge_variables(item['config']['variables'],
                legacy.config.get('variables') if legacy else {},*[s.config.get('variables') for s in splits],
                existing.config.get('variables') if existing else {})
            target_name=legacy.name if legacy else item['name']
            if ApiInterfaceCase.objects.filter(project=project,name=target_name).exists():raise ValidationError('目标接口用例名称已存在：'+target_name)
            native=ApiInterfaceCase.objects.create(project=project,created_by=user,name=target_name,
                description=legacy.description if legacy else item['description'],priority=legacy.priority if legacy else 'P2',
                group=legacy.group if legacy else group,interface=prepared[-1][2],config=base,file_ids=legacy.file_ids if legacy else [])
            for index,(source,scene_step,interface,data) in enumerate(prepared,1):
                ApiInterfaceCaseStep.objects.create(interface_case=native,origin_interface=interface,order=index,
                    role='main' if index==len(prepared) else 'precondition',name=scene_step.name if scene_step else source['name'],
                    interface_data=data,config=copy.deepcopy(scene_step.config if scene_step else {'excel_source_id':source['source_id']}),
                    file_ids=scene_step.file_ids if scene_step else [],sync_fields=scene_step.sync_fields if scene_step else [])
            tag_ids=set(legacy.tags.values_list('pk',flat=True)) if legacy else set()
            for split in splits:tag_ids.update(split.tags.values_list('pk',flat=True))
            native.tags.set(tag_ids)
            native.config[META]['assets']={s['source_id']:{'interface_id':interface.pk,'interface_case_id':native.pk} for s,_,interface,_ in prepared}
            native.save(update_fields=['config','updated_at'])
            created+=1;created_steps+=len(prepared)
            native_ids.append(native.pk)
            # Keep old records and reports accessible by ID; default lists and execution omit them.
            if legacy and not legacy.config[META].get(RETIRED):
                legacy.config={**legacy.config,META:{**legacy.config[META],RETIRED:native.pk}}
                legacy.save(update_fields=['config','updated_at']);retired_scenes+=1
            for split in splits:
                if not split.config[META].get(RETIRED):
                    split.config={**split.config,META:{**split.config[META],RETIRED:native.pk}}
                    split.save(update_fields=['config','updated_at']);retired_splits+=1
        return {**resources,'destination':'interface_cases','created_cases':0,'updated_cases':updated,'ids':native_ids,
                'created_interface_cases':created,'created_interfaces':created_interfaces,'created_steps':created_steps,
                'interface_ids':list(dict.fromkeys(interface_ids)),'converted_scenarios':retired_scenes,'consolidated_split_cases':retired_splits}
