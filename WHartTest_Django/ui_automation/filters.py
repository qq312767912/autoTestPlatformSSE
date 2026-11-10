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
