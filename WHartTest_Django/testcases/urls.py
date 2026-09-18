"""testcases 应用的非嵌套路由。

用例相关资源（testcases / testcase-reviews / testcase-modules / test-suites /
test-executions）都挂在项目嵌套路由下，见 ``wharttest_django/urls.py`` 的
``projects_router``，不在本文件注册。

本文件只放不属于任何项目的平台级资源：

* ``review-llm-config``：用例审查专用 LLM 配置（平台级单例，仅平台管理员可维护）
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import TestCaseReviewLLMConfigViewSet

app_name = "testcases"

router = DefaultRouter()
router.register(
    r"review-llm-config",
    TestCaseReviewLLMConfigViewSet,
    basename="testcase-review-llm-config",
)

urlpatterns = [
    path("", include(router.urls)),
]
