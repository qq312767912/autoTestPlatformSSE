"""权限目录完整性、中文显示、授予/撤销及组织继承回归。"""
import re
from django.contrib.auth.models import User, Group, Permission
from rest_framework.test import APITestCase
from accounts.serializers import PermissionSerializer
from accounts.views import PermissionViewSet
from projects.models import Project, ProjectMember


class PermissionAccessTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="permission-admin", password=None)
        self.user = User.objects.create_user(username="permission-member")
        self.group = Group.objects.create(name="权限回归组织")
        self.user.groups.add(self.group)
        self.direct = list(PermissionViewSet.queryset[:45])
        self.inherited = list(PermissionViewSet.queryset[45:55])
        self.user.user_permissions.set(self.direct)
        self.group.permissions.set(self.inherited)
        self.project = Project.objects.create(name="权限回归项目", creator=self.admin)
        ProjectMember.objects.create(project=self.project, user=self.user, role="member")
        self.url = f"/api/accounts/users/{self.user.pk}/permissions/"

    def test_effective_permissions_are_complete_and_not_paginated(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url, {"page_size": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p["id"] for p in response.data}, {p.pk for p in self.direct + self.inherited})

    def test_scopes_distinguish_direct_and_inherited_permissions(self):
        self.client.force_authenticate(self.admin)
        for scope, permissions in [("direct", self.direct), ("inherited", self.inherited)]:
            response = self.client.get(self.url, {"scope": scope})
            self.assertEqual(response.status_code, 200)
            self.assertEqual({p["id"] for p in response.data}, {p.pk for p in permissions})
        self.assertEqual(self.client.get(self.url, {"scope": "invalid"}).status_code, 400)

    def test_save_and_revoke_take_effect_without_relogin(self):
        self.client.force_authenticate(self.admin)
        permission = Permission.objects.get(content_type__app_label="code_analysis", codename="view_analysistask")
        url = f"/api/accounts/users/{self.user.pk}/update-permissions/"
        self.assertEqual(self.client.put(url, {"permission_ids": [permission.pk]}, format="json").status_code, 200)
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))
        self.assertTrue(any(p["id"] == permission.pk for p in self.client.get(self.url).data))
        self.assertEqual(self.client.get(f'/api/code-analysis/tasks/?project={self.project.pk}').status_code, 200)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.put(url, {"permission_ids": []}, format="json").status_code, 200)
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))
        self.assertEqual(self.client.get(f'/api/code-analysis/tasks/?project={self.project.pk}').status_code, 403)
        self.assertEqual({p["id"] for p in self.client.get(self.url).data}, {p.pk for p in self.inherited})

    def test_permission_catalog_has_no_english_display_labels_or_duplicate_ids(self):
        permissions = list(PermissionViewSet.queryset.select_related("content_type"))
        data = PermissionSerializer(permissions, many=True).data
        self.assertEqual(len({p["id"] for p in data}), len(data))
        for p in data:
            fields = [p["name_cn"], p["content_type"]["model_cn"], p["content_type"]["app_label_cn"], p["content_type"]["app_label_subcategory"]]
            self.assertFalse(any(re.search(r"[A-Za-z]", str(value or "")) for value in fields), fields)

    def test_ordinary_user_cannot_grant_themselves_permissions(self):
        self.client.force_authenticate(self.user)
        url = f"/api/accounts/users/{self.user.pk}/update-permissions/"
        self.assertEqual(self.client.put(url, {"permission_ids": [self.inherited[0].pk]}, format="json").status_code, 403)

    def test_code_review_grant_adds_project_context_but_not_membership(self):
        self.client.force_authenticate(self.admin)
        permission = Permission.objects.get(content_type__app_label="code_analysis", codename="view_analysistask")
        url = f"/api/accounts/users/{self.user.pk}/update-permissions/"
        response = self.client.put(url, {"permission_ids": [permission.pk]}, format="json")
        self.assertEqual(response.status_code, 200)
        fresh = User.objects.get(pk=self.user.pk)
        self.assertTrue(fresh.has_perm("projects.view_project"))
        self.assertEqual(ProjectMember.objects.filter(user=fresh).count(), 1)

    def test_explicit_code_connection_permissions_work_for_nonstaff(self):
        perms = Permission.objects.filter(content_type__app_label="code_analysis", content_type__model="gitlabconnection")
        self.user.user_permissions.set(perms)
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))
        url = "/api/code-analysis/connections/"
        response = self.client.post(url, {"name": "权限回归连接", "base_url": "https://example.invalid"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data["id"]
        self.assertEqual(self.client.patch(f"{url}{pk}/", {"name": "修改后的连接"}, format="json").status_code, 200)
        self.assertIn(self.client.delete(f"{url}{pk}/").status_code, (200, 204))
        self.user.user_permissions.clear()
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))
        self.assertEqual(self.client.post(url, {"name": "无权连接", "base_url": "https://example.invalid"}, format="json").status_code, 403)
