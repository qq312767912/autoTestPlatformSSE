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


def is_test_lead_anywhere(user) -> bool:
    """是否在**任一**项目里是测试负责人（超级用户恒真）。

    Skill Hub 是公共目录，不再等同于"某个项目的资源"：一份 Skill 可能由 A 项目
    上传、却被 B 项目的人使用。若管理权仍只认 URL 里那个项目，A 项目的负责人就
    管不了自己上传的那份——所以公共目录的管理权按"是否在某个项目里是负责人"判定。

    刻意不放宽到"所有登录用户"：公共**可见**不等于公共**可改**。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ProjectMember.objects.filter(user=user, role__in=TEST_LEAD_ROLES).exists()


def is_test_executor_anywhere(user) -> bool:
    """是否在**任一**项目里是测试执行人员（即任一项目成员；超级用户恒真）。

    与 ``is_test_lead_anywhere`` 同一套理由：公共目录里的条目不属于某一个项目，
    所以"能不能操作它"按"在某个项目里有没有这个角色"判定，而不是按 URL 里的那个项目。

    ``TEST_EXECUTOR_ROLES`` 包含 owner/admin/member，所以这条实际等价于
    "至少是某个项目的成员"。非成员（与未登录）仍被挡在外面。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ProjectMember.objects.filter(user=user, role__in=TEST_EXECUTOR_ROLES).exists()


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


class IsTestLeadAnywhere(permissions.BasePermission):
    """公共目录（Skill Hub）的管理权：超管，或在任一项目里是测试负责人。

    与 ``IsTestLead`` 的区别：后者把管理权锚定在 URL 里的那个项目，用于"作用于
    该项目自己的资源"的动作；本类用于**公共、不归属任何单一项目**的资源
    （例如给 Skill 补填能力阶段）。
    """

    message = "该操作仅允许平台管理员或项目测试负责人执行"

    def has_permission(self, request, view):
        return is_test_lead_anywhere(request.user)


class IsTestExecutorAnywhere(permissions.BasePermission):
    """公共目录（Skill Hub）的执行级动作：超管，或在任一项目里是测试执行人员/负责人。

    用于"不归属任何单一项目、但比纯读取重一点"的动作，例如导出 Skill 包、
    重跑包校验。纯读取（详情、内容、版本史）不要求这条，见
    ``skills.views.SkillViewSet.GLOBAL_READ_ACTIONS``。
    """

    message = "该操作仅允许平台管理员或项目测试执行人员执行"

    def has_permission(self, request, view):
        return is_test_executor_anywhere(request.user)
