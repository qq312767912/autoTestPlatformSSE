from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError,PermissionDenied
from projects.models import Project
from .excel_import import build_plan
from .excel_interface_import import persist_interface_plan,preview_existing

def import_interface_excel(view,request):
    project=get_object_or_404(Project,pk=view.kwargs.get('project_pk'))
    upload=request.FILES.get('file')
    if not upload:raise ValidationError('请选择 Excel 文件')
    plan=build_plan(upload)
    existing=preview_existing(plan,project)
    if str(request.data.get('confirm','')).lower()=='true':
        if request.data.get('sha256')!=plan['sha256']:raise ValidationError('文件与预览不一致，请重新预览')
        permissions=['api_interfaces.add_apiinterface','api_testcases.add_apiinterfacecase']
        if plan.get('environments'):permissions+=['api_environments.add_apienvironment','api_environments.add_apienvironmentvariable']
        if not all(request.user.has_perm(p) for p in permissions):raise PermissionDenied('完整导入需要新增接口、接口用例、环境及环境变量权限')
        if existing['updatable'] and not request.user.has_perm('api_testcases.change_apiinterfacecase'):raise PermissionDenied('补全已有导入需要修改接口用例权限')
        if existing['legacy_scene_count'] and not request.user.has_perm('api_testcases.change_apitestcase'):raise PermissionDenied('迁移旧版场景记录需要修改场景权限')
        result=persist_interface_plan(plan,project,request.user,request.data.get('group_id'),str(request.data.get('update_existing','')).lower()=='true')
        return Response({**result,'warnings':plan['warnings']},status=201)
    return Response({key:plan[key] for key in ['format','sha256','warnings','errors','case_count','step_count','required_variables',
        'ignored_history_rows','environment_count','recovered_variable_count','data_row_count','sheet_counts']} |
        {**existing,'destination':'interface_cases','cases':[{'name':c['name'],'source_id':c['source_id'],
            'steps':[{'name':s['name'],'method':s['interface_data']['method'],'url':s['interface_data']['url'],'order':i+1,
                      'assertions':len(s['interface_data']['validators'])} for i,s in enumerate(c['steps'])]} for c in plan['cases']]})
