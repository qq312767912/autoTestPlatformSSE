"""旧分组缺少名称时显式使用源ID，不伪造原模块名称。"""
from rest_framework.exceptions import PermissionDenied
from api_modules.models import ApiModule
from api_interfaces.models import ApiInterface

def assign_import_module(project,user,item,interfaces,preferred_id=None,fresh_ids=()):
    missing=[interface.pk for interface in interfaces if not interface.module_id]
    if not missing:return None,0
    if set(missing)-set(fresh_ids) and not user.has_perm('api_interfaces.change_apiinterface'):raise PermissionDenied('补全已有接口模块需要修改接口权限')
    module=ApiModule.objects.filter(project=project,pk=preferred_id).first() if preferred_id else None
    source_group=item.get('source_group_id')
    if not module:
        name=('旧平台分组 '+source_group if source_group else item['name'])[:100]
        module=ApiModule.objects.filter(project=project,parent__isnull=True,name=name).order_by('pk').first()
        if not module:
            if not user.has_perm('api_modules.add_apimodule'):raise PermissionDenied('导入模块需要新增接口模块权限')
            module=ApiModule.objects.create(project=project,created_by=user,name=name,
                description=('源Excel仅提供旧分组ID '+source_group+'，未提供名称，可在平台中重命名。') if source_group else '源Excel无模块信息，按原用例名称归类。')
    count=ApiInterface.objects.filter(project=project,pk__in=missing,module__isnull=True).update(module=module)
    return module.pk,count
