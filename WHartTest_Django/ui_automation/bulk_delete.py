"""批量删除复用单条删除规则；仅删除显式选中且当前用户可访问的资产。"""
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError, RestrictedError
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from projects.models import Project


def delete_selected_assets(view, request):
    model = view.queryset.model
    if not request.user.has_perm(f'ui_automation.delete_{model._meta.model_name}'):
        raise PermissionDenied('缺少删除权限')
    try:
        project_id = int(request.data.get('project'))
        if project_id <= 0: raise ValueError()
    except (TypeError, ValueError):
        raise ValidationError('请选择有效项目')
    raw = request.data.get('ids')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100 or any(type(x) is not int or x <= 0 for x in raw):
        raise ValidationError('请选择1至100条有效数据')
    project = Project.objects.filter(pk=project_id)
    if not request.user.is_superuser:
        project = project.filter(Q(creator=request.user) | Q(members__user=request.user))
    if not project.exists():
        raise PermissionDenied('无权访问该项目')
    ids = list(dict.fromkeys(raw))
    deleted, blocked = [], []
    previous = dict(view.kwargs)
    with transaction.atomic():
        rows = list(model.objects.select_for_update().filter(project_id=project_id, pk__in=ids).order_by('pk'))
        if len(rows) != len(ids):
            raise ValidationError('选择范围包含不存在或不属于当前项目的数据，未执行删除')
        try:
            for item in rows:
                view.kwargs['pk'] = item.pk
                try:
                    with transaction.atomic():
                        response = view.destroy(request, pk=item.pk)
                    if response.status_code in (200, 204):
                        deleted.append(item.pk)
                    else:
                        blocked.append({'id': item.pk, 'name': item.name, 'reason': response.data.get('error', '存在关联，无法删除')})
                except (ProtectedError, RestrictedError):
                    blocked.append({'id': item.pk, 'name': item.name, 'reason': '存在关联引用，请先解除引用或移动相关资产'})
        finally:
            view.kwargs = previous
    return Response({'deleted_count': len(deleted), 'deleted_ids': deleted, 'blocked': blocked})
