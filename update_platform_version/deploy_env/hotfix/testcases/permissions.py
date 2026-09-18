from rest_framework import permissions
from projects.models import ProjectMember  # 用于检查项目成员


class IsProjectMemberForTestCase(permissions.BasePermission):
    """
    自定义权限，用于检查用户是否是与 TestCase 关联的项目的成员。
    允许项目所有者、管理员和普通成员访问。
    超级管理员(is_superuser=True)可以访问所有项目。
    """

    def has_permission(self, request, view):
        """
        检查用户是否有权限访问列表视图或创建操作。
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有项目
        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        """
        检查用户是否对单个对象（TestCase 实例）有权限。
        obj 是 TestCase 实例。
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有对象
        if request.user.is_superuser:
            return True

        # TestCase 对象应该有一个 project 属性
        if not hasattr(obj, "project"):
            return False  # 对象没有关联项目，不应该发生

        # 检查用户是否是该 TestCase 所属项目的成员
        return ProjectMember.objects.filter(
            project=obj.project,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()


# 如果需要“仅创建者可写”的更细粒度权限，可在此补充对应权限类实现。


class IsProjectMemberForTestCaseModule(permissions.BasePermission):
    """
    自定义权限，用于检查用户是否是与 TestCaseModule 关联的项目的成员。
    允许项目所有者、管理员和普通成员访问。
    超级管理员(is_superuser=True)可以访问所有项目。
    """

    def has_permission(self, request, view):
        """
        检查用户是否有权限访问列表视图或创建操作。
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有项目
        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        """
        检查用户是否对单个对象（TestCaseModule 实例）有权限。
        obj 是 TestCaseModule 实例。
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有对象
        if request.user.is_superuser:
            return True

        # TestCaseModule 对象应该有一个 project 属性
        if not hasattr(obj, "project"):
            return False  # 对象没有关联项目，不应该发生

        # 检查用户是否是该 TestCaseModule 所属项目的成员
        return ProjectMember.objects.filter(
            project=obj.project,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()


class IsProjectMemberForTestSuite(permissions.BasePermission):
    """
    自定义权限，用于检查用户是否是与 TestSuite 关联的项目的成员。
    超级管理员(is_superuser=True)可以访问所有项目。
    """

    def has_permission(self, request, view):
        """检查用户是否有权限访问列表视图或创建操作"""
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有项目
        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        """检查用户是否对单个TestSuite实例有权限"""
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有对象
        if request.user.is_superuser:
            return True

        if not hasattr(obj, "project"):
            return False

        return ProjectMember.objects.filter(
            project=obj.project,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()


class IsProjectMemberForTestExecution(permissions.BasePermission):
    """
    自定义权限，用于检查用户是否是与 TestExecution 关联的项目的成员。
    超级管理员(is_superuser=True)可以访问所有项目。
    """

    def has_permission(self, request, view):
        """检查用户是否有权限访问列表视图或创建操作"""
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有项目
        if request.user.is_superuser:
            return True

        project_pk = view.kwargs.get("project_pk")
        if not project_pk or str(project_pk).lower() in ("none", "null", "undefined"):
            return False

        try:
            project_pk = int(project_pk)
        except (ValueError, TypeError):
            return False

        return ProjectMember.objects.filter(
            project_id=project_pk,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()

    def has_object_permission(self, request, view, obj):
        """检查用户是否对单个TestExecution实例有权限"""
        if not request.user or not request.user.is_authenticated:
            return False

        # 超级管理员可以访问所有对象
        if request.user.is_superuser:
            return True

        # TestExecution通过suite关联到project
        if not hasattr(obj, "suite") or not hasattr(obj.suite, "project"):
            return False

        return ProjectMember.objects.filter(
            project=obj.suite.project,
            user=request.user,
            role__in=["owner", "admin", "member"],
        ).exists()


class IsPlatformAdmin(permissions.BasePermission):
    """仅平台管理员（is_staff 或 is_superuser）可访问。

    用例审查的专用 LLM 配置属于平台级凭据：普通项目成员即使拥有 testcases
    应用的模型权限，也不应看到 API 地址与密钥是否已配置，更不能改动它。
    因此该视图集不使用 ``HasModelPermission``（它按模型权限放行），而是整体
    收紧到管理员。
    """

    message = "仅平台管理员可维护用例审查专用 LLM 配置"

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return bool(user.is_superuser or user.is_staff)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
