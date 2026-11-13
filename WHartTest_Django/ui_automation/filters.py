"""UI 用例模块筛选包含当前模块和所有后代模块。"""
from django_filters import rest_framework as filters

from .models import UiModule, UiTestCase


class UiTestCaseFilter(filters.FilterSet):
    module = filters.ModelChoiceFilter(
        queryset=UiModule.objects.all(), method="filter_module_tree",
    )

    class Meta:
        model = UiTestCase
        fields = ["project", "module", "level", "status"]

    def filter_module_tree(self, queryset, name, module):
        return queryset.filter(
            project_id=module.project_id,
            module_id__in=module.get_all_descendant_ids(),
        )


from django.db.models import Case, When, IntegerField
from rest_framework.filters import OrderingFilter


class UiModuleTreeOrderingFilter(OrderingFilter):
    """默认按模块树顺序排列用例；显式排序仍遵循用户选择。"""
    def filter_queryset(self, request, queryset, view):
        if request.query_params.get(self.ordering_param):
            return super().filter_queryset(request, queryset, view)
        modules = UiModule.objects.all()
        project = request.query_params.get('project')
        if project:
            modules = modules.filter(project_id=project)
        rows = {row['id']: row for row in modules.values('id', 'parent_id', 'order', 'project_id')}
        def path(module_id, seen=None):
            seen = set() if seen is None else seen
            if module_id not in rows or module_id in seen:
                return ()
            seen.add(module_id)
            row = rows[module_id]
            return path(row['parent_id'], seen) + ((row['order'], row['id']),)
        ordered = sorted(rows, key=lambda key: (rows[key]['project_id'], path(key)))
        if not ordered:
            return queryset.order_by('-created_at', 'id')
        return queryset.annotate(_module_rank=Case(
            *[When(module_id=key, then=index) for index, key in enumerate(ordered)],
            default=len(ordered), output_field=IntegerField(),
        )).order_by('_module_rank', '-created_at', 'id')
