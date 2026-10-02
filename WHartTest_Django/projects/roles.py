"""项目内业务角色判定（Skill Hub / 质量飞轮共用）。

平台的项目成员角色只有 ``owner`` / ``admin`` / ``member`` 三种，而 Skill Hub
需要更贴近测试业务的角色划分：

- **测试负责人**：审批、激活、回滚、隔离 Skill 版本与能力发布。
- **测试执行人员**：上传候选包、触发预检与评测、查看结果、提交反馈。

映射沿用平台既有约定（见 ``knowledge_evolution.operations.ProjectQualityCockpitService``
与 ``knowledge_evolution.views._ensure_test_lead``）：``owner`` / ``admin`` 为测试
负责人，``member`` 为测试执行人员；超级用户视作测试负责人。项目角色是基础权限
来源，不在业务界面暴露角色名。
"""
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from .models import ProjectMember

# 项目角色 -> 业务角色的映射真值，所有模块统一从这里读取，避免各处硬编码分叉。
TEST_LEAD_ROLES = ("owner", "admin")
TEST_EXECUTOR_ROLES = ("owner", "admin", "member")

BUSINESS_ROLE_LEAD = "test_lead"
BUSINESS_ROLE_EXECUTOR = "test_executor"


def get_project_role(user, project_id) -> str | None:
    """返回用户在项目中的项目角色（owner/admin/member），非成员返回 None。"""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if not project_id:
        return None
    return (
        ProjectMember.objects.filter(user=user, project_id=project_id)
        .values_list("role", flat=True)
        .first()
    )


def is_project_member(user, project_id) -> bool:
    """超级用户对任意项目可见，其余按成员关系判定。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ProjectMember.objects.filter(user=user, project_id=project_id).exists()


def is_test_lead(user, project_id) -> bool:
    """是否具备测试负责人权限。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ProjectMember.objects.filter(
        user=user, project_id=project_id, role__in=TEST_LEAD_ROLES
    ).exists()


def is_test_executor(user, project_id) -> bool:
    """是否具备测试执行人员权限（负责人天然具备）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ProjectMember.objects.filter(
        user=user, project_id=project_id, role__in=TEST_EXECUTOR_ROLES
    ).exists()


def business_role(user, project_id) -> str | None:
    """返回业务角色标识，用于前端按角色呈现按钮（不做权限判定）。"""
    if is_test_lead(user, project_id):
        return BUSINESS_ROLE_LEAD
    if is_test_executor(user, project_id):
        return BUSINESS_ROLE_EXECUTOR
    return None


def visible_project_ids(user):
    """返回用户可访问的项目 ID 集合；超级用户返回 None 表示不限制。"""
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if user.is_superuser:
        return None
    return list(
        ProjectMember.objects.filter(user=user).values_list("project_id", flat=True)
    )


def ensure_project_access(user, project_id) -> None:
    """断言用户可访问项目，否则抛 403。"""
    if not is_project_member(user, project_id):
        raise PermissionDenied("无权访问该项目")


def ensure_test_executor(user, project_id) -> None:
    """断言用户是测试执行人员（或负责人），否则抛 403。"""
    if not is_test_executor(user, project_id):
        raise PermissionDenied("该操作需要项目测试执行人员权限")


def ensure_test_lead(user, project_id) -> None:
    """断言用户是测试负责人，否则抛 403。

    文案与 ``knowledge_evolution.views._ensure_test_lead`` 保持一致，
    避免同一语义出现两套提示。
    """
    if not is_test_lead(user, project_id):
        raise PermissionDenied("该操作仅允许测试负责人执行")


def extract_project_id(view) -> str | None:
    """从嵌套路由参数中提取项目 ID，兼容 ``project_pk`` 与 ``pk`` 两种写法。"""
    kwargs = getattr(view, "kwargs", None) or {}
    return kwargs.get("project_pk") or kwargs.get("project_id") or kwargs.get("pk")


class IsProjectScoped(permissions.BasePermission):
    """要求请求者是可访问该项目（成员或超级用户）的查询集级权限。

    项目不存在时由视图层的 ``get_object_or_404`` 返回 404；项目存在但用户不是
    成员时返回 403，避免通过响应码差异泄露其他项目是否存在。
    """

    message = "无权访问该项目"

    def has_permission(self, request, view):
        return is_project_member(request.user, extract_project_id(view))


class IsTestExecutor(IsProjectScoped):
    """允许上传、预检、评测等执行类动作，仅测试执行人员与负责人可用。"""

    message = "该操作需要项目测试执行人员权限"

    def has_permission(self, request, view):
        return is_test_executor(request.user, extract_project_id(view))


class IsTestLead(IsProjectScoped):
    """允许审批、激活、回滚、隔离等治理类动作，仅测试负责人可用。"""

    message = "该操作仅允许测试负责人执行"

    def has_permission(self, request, view):
        return is_test_lead(request.user, extract_project_id(view))
