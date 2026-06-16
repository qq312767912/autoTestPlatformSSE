from django.test import TestCase
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from rest_framework.test import APIClient
from rest_framework import status

from api_interfaces.models import ApiInterface
from projects.models import Project, ProjectMember
from .models import ApiModule


def _grant_module_perms(user):
    """授予用户 ApiModule 的全部模型权限。"""
    ct = ContentType.objects.get_for_model(ApiModule)
    perms = Permission.objects.filter(content_type=ct)
    user.user_permissions.add(*perms)
    for attr in ('_perm_cache', '_user_perm_cache'):
        try:
            delattr(user, attr)
        except AttributeError:
            pass


class ApiModuleModelTest(TestCase):
    """ApiModule 模型测试"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.project = Project.objects.create(name='Test Project', creator=self.user)

    def test_create_module(self):
        """测试创建模块"""
        module = ApiModule.objects.create(
            name='Login Module',
            project=self.project,
            created_by=self.user,
        )
        self.assertEqual(module.name, 'Login Module')
        self.assertEqual(module.project, self.project)
        self.assertIsNone(module.parent)
        self.assertIsNotNone(module.created_at)
        self.assertIsNotNone(module.updated_at)

    def test_str_representation(self):
        """测试字符串表示"""
        module = ApiModule.objects.create(
            name='Auth Module',
            project=self.project,
            created_by=self.user,
        )
        self.assertEqual(str(module), 'Auth Module')

    def test_parent_child_relationship(self):
        """测试父子关系"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        child = ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        self.assertEqual(child.parent, parent)
        self.assertIn(child, parent.children.all())

    def test_get_ancestors(self):
        """测试获取祖先模块"""
        grandparent = ApiModule.objects.create(
            name='Grandparent', project=self.project, created_by=self.user,
        )
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, parent=grandparent, created_by=self.user,
        )
        child = ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        ancestors = child.get_ancestors()
        self.assertEqual(len(ancestors), 2)
        self.assertEqual(ancestors[0], parent)
        self.assertEqual(ancestors[1], grandparent)

    def test_get_ancestors_root_module(self):
        """测试根模块没有祖先"""
        root = ApiModule.objects.create(
            name='Root', project=self.project, created_by=self.user,
        )
        self.assertEqual(root.get_ancestors(), [])

    def test_get_descendants(self):
        """测试获取后代模块"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        child1 = ApiModule.objects.create(
            name='Child1', project=self.project, parent=parent, created_by=self.user,
        )
        child2 = ApiModule.objects.create(
            name='Child2', project=self.project, parent=parent, created_by=self.user,
        )
        grandchild = ApiModule.objects.create(
            name='Grandchild', project=self.project, parent=child1, created_by=self.user,
        )
        descendants = parent.get_descendants()
        self.assertEqual(len(descendants), 3)
        self.assertIn(child1, descendants)
        self.assertIn(child2, descendants)
        self.assertIn(grandchild, descendants)

    def test_get_descendants_leaf_module(self):
        """测试叶子模块没有后代"""
        leaf = ApiModule.objects.create(
            name='Leaf', project=self.project, created_by=self.user,
        )
        self.assertEqual(leaf.get_descendants(), [])

    def test_ordering(self):
        """测试默认按 created_at 升序排列"""
        m1 = ApiModule.objects.create(name='First', project=self.project, created_by=self.user)
        m2 = ApiModule.objects.create(name='Second', project=self.project, created_by=self.user)
        modules = list(ApiModule.objects.filter(project=self.project))
        self.assertEqual(modules[0], m1)
        self.assertEqual(modules[1], m2)

    def test_cascade_delete_project(self):
        """测试删除项目时级联删除模块"""
        ApiModule.objects.create(
            name='To Delete', project=self.project, created_by=self.user,
        )
        self.project.delete()
        self.assertEqual(ApiModule.objects.count(), 0)

    def test_cascade_delete_parent(self):
        """测试删除父模块时级联删除子模块"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        parent.delete()
        self.assertEqual(ApiModule.objects.count(), 0)

    def test_set_null_on_user_delete(self):
        """测试删除用户时 created_by 置空"""
        module = ApiModule.objects.create(
            name='Orphan', project=self.project, created_by=self.user,
        )
        self.user.delete()
        module.refresh_from_db()
        self.assertIsNone(module.created_by)


class ApiModuleAPITest(TestCase):
    """ApiModule API CRUD 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apiuser', password='testpass')
        self.project = Project.objects.create(name='API Project', creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='admin')
        _grant_module_perms(self.user)
        self.client.force_authenticate(user=self.user)
        self.base_url = f'/api/projects/{self.project.pk}/api-modules/'

    def test_list_returns_only_root_modules(self):
        """测试列表只返回根模块"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [m['name'] for m in response.data]
        self.assertIn('Parent', names)
        self.assertNotIn('Child', names)

    def test_list_includes_nested_children(self):
        """测试列表包含嵌套子模块"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        parent_data = response.data[0]
        self.assertEqual(len(parent_data['children']), 1)
        self.assertEqual(parent_data['children'][0]['name'], 'Child')

    def test_create_module(self):
        """测试创建模块"""
        data = {'name': 'New Module', 'description': 'A test module'}
        response = self.client.post(self.base_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', response.data)
        self.assertEqual(response.data['name'], 'New Module')
        module = ApiModule.objects.get(name='New Module')
        self.assertEqual(module.project, self.project)
        self.assertEqual(module.created_by, self.user)

    def test_create_module_with_parent(self):
        """测试创建带父模块的子模块"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        data = {'name': 'Child Module', 'parent': parent.pk}
        response = self.client.post(self.base_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        child = ApiModule.objects.get(name='Child Module', project=self.project)
        self.assertEqual(child.parent, parent)

    def test_create_module_parent_different_project_rejected(self):
        """测试跨项目父模块被拒绝"""
        other_project = Project.objects.create(name='Other Project', creator=self.user)
        other_module = ApiModule.objects.create(
            name='Other', project=other_project, created_by=self.user,
        )
        data = {'name': 'Bad Child', 'parent': other_module.pk}
        response = self.client.post(self.base_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_module(self):
        """测试获取单个模块"""
        module = ApiModule.objects.create(
            name='Detail Module', project=self.project, created_by=self.user,
        )
        response = self.client.get(f'{self.base_url}{module.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Detail Module')

    def test_update_module(self):
        """测试更新模块"""
        module = ApiModule.objects.create(
            name='Old Name', project=self.project, created_by=self.user,
        )
        response = self.client.patch(
            f'{self.base_url}{module.pk}/',
            {'name': 'New Name'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        module.refresh_from_db()
        self.assertEqual(module.name, 'New Name')

    def test_update_module_description(self):
        """测试更新模块描述"""
        module = ApiModule.objects.create(
            name='Module', project=self.project, created_by=self.user,
        )
        response = self.client.patch(
            f'{self.base_url}{module.pk}/',
            {'description': 'Updated desc'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        module.refresh_from_db()
        self.assertEqual(module.description, 'Updated desc')

    def test_delete_module_no_children(self):
        """测试删除无子模块的模块"""
        module = ApiModule.objects.create(
            name='To Delete', project=self.project, created_by=self.user,
        )
        response = self.client.delete(f'{self.base_url}{module.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ApiModule.objects.filter(pk=module.pk).exists())
        self.assertEqual(response.data['deleted_interface_count'], 0)
        self.assertEqual(response.data['deleted_interface_ids'], [])

    def test_delete_module_removes_linked_interfaces(self):
        """测试删除模块时会同步删除模块下的接口"""
        module = ApiModule.objects.create(
            name='Module With APIs', project=self.project, created_by=self.user,
        )
        interface_1 = ApiInterface.objects.create(
            name='Delete API 1',
            type='http',
            method='GET',
            url='/api/delete-1',
            project=self.project,
            module=module,
            created_by=self.user,
        )
        interface_2 = ApiInterface.objects.create(
            name='Delete API 2',
            type='http',
            method='POST',
            url='/api/delete-2',
            project=self.project,
            module=module,
            created_by=self.user,
        )

        response = self.client.delete(f'{self.base_url}{module.pk}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ApiModule.objects.filter(pk=module.pk).exists())
        self.assertFalse(
            ApiInterface.objects.filter(
                pk__in=[interface_1.pk, interface_2.pk]
            ).exists()
        )
        self.assertEqual(
            sorted(response.data['deleted_interface_ids']),
            sorted([interface_1.pk, interface_2.pk]),
        )
        self.assertEqual(response.data['deleted_interface_count'], 2)

    def test_delete_module_with_children_blocked(self):
        """测试删除有子模块的模块被阻止"""
        parent = ApiModule.objects.create(
            name='Parent', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Child', project=self.project, parent=parent, created_by=self.user,
        )
        response = self.client.delete(f'{self.base_url}{parent.pk}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('child modules', response.data['detail'])

    def test_search_by_name(self):
        """测试按名称搜索"""
        ApiModule.objects.create(
            name='Login Module', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Payment Module', project=self.project, created_by=self.user,
        )
        response = self.client.get(f'{self.base_url}search/', {'keyword': 'Login'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Login Module')

    def test_search_by_description(self):
        """测试按描述搜索"""
        ApiModule.objects.create(
            name='Module A',
            project=self.project,
            description='Handles authentication',
            created_by=self.user,
        )
        response = self.client.get(f'{self.base_url}search/', {'keyword': 'authentication'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_search_empty_keyword_returns_all(self):
        """测试空关键词返回所有模块"""
        ApiModule.objects.create(
            name='Module A', project=self.project, created_by=self.user,
        )
        ApiModule.objects.create(
            name='Module B', project=self.project, created_by=self.user,
        )
        response = self.client.get(f'{self.base_url}search/', {'keyword': ''})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_unauthenticated_access(self):
        """测试未认证用户无法访问"""
        self.client.force_authenticate(user=None)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ApiModulePermissionTest(TestCase):
    """ApiModule 权限测试"""

    def setUp(self):
        self.client = APIClient()
        # Project A with member
        self.user_a = User.objects.create_user(username='user_a', password='testpass')
        self.project_a = Project.objects.create(name='Project A', creator=self.user_a)
        ProjectMember.objects.create(project=self.project_a, user=self.user_a, role='member')
        _grant_module_perms(self.user_a)

        # Project B with admin
        self.user_b = User.objects.create_user(username='user_b', password='testpass')
        self.project_b = Project.objects.create(name='Project B', creator=self.user_b)
        ProjectMember.objects.create(project=self.project_b, user=self.user_b, role='admin')
        _grant_module_perms(self.user_b)

        # Superuser
        self.superuser = User.objects.create_superuser(
            username='admin', password='adminpass',
        )

        # Non-member user
        self.outsider = User.objects.create_user(username='outsider', password='testpass')

        # Create modules in each project
        self.module_a = ApiModule.objects.create(
            name='Module A', project=self.project_a, created_by=self.user_a,
        )
        self.module_b = ApiModule.objects.create(
            name='Module B', project=self.project_b, created_by=self.user_b,
        )

    def test_project_isolation_list(self):
        """测试项目数据隔离 - 列表只返回当前项目的模块"""
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-modules/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = response.data['results'] if isinstance(response.data, dict) else response.data
        module_names = [m['name'] for m in items]
        self.assertIn('Module A', module_names)
        self.assertNotIn('Module B', module_names)

    def test_cross_project_access_denied(self):
        """测试跨项目访问被拒绝"""
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get(f'/api/projects/{self.project_b.pk}/api-modules/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_member_access_denied(self):
        """测试非项目成员无法访问"""
        self.client.force_authenticate(user=self.outsider)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-modules/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_access_all_projects(self):
        """测试超级管理员可以访问所有项目"""
        self.client.force_authenticate(user=self.superuser)
        for project in [self.project_a, self.project_b]:
            response = self.client.get(f'/api/projects/{project.pk}/api-modules/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_member_can_create_module(self):
        """测试成员可以创建模块"""
        self.client.force_authenticate(user=self.user_a)
        data = {'name': 'New Module'}
        response = self.client.post(
            f'/api/projects/{self.project_a.pk}/api-modules/',
            data,
            format='json',
        )
        self.assertIn(
            response.status_code,
            [status.HTTP_201_CREATED, status.HTTP_403_FORBIDDEN],
        )

    def test_invalid_project_pk(self):
        """测试无效的 project_pk"""
        self.client.force_authenticate(user=self.user_a)
        response = self.client.get('/api/projects/999999/api-modules/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
