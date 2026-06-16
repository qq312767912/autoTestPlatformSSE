from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from rest_framework.test import APIClient
from rest_framework import status

from projects.models import Project, ProjectMember
from api_interfaces.models import ApiInterface
from api_testcases.models import ApiTestCase, ApiTestCaseStep
from .models import ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig


def _grant_model_perms(user, model_class):
    ct = ContentType.objects.get_for_model(model_class)
    perms = Permission.objects.filter(content_type=ct)
    user.user_permissions.add(*perms)


def _grant_all_sync_perms(user):
    for model_cls in [ApiSyncConfig, ApiSyncHistory, ApiGlobalSyncConfig]:
        _grant_model_perms(user, model_cls)
    for attr in ('_perm_cache', '_user_perm_cache'):
        try:
            delattr(user, attr)
        except AttributeError:
            pass


class ApiSyncConfigModelTest(TestCase):
    """ApiSyncConfig model tests"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.project = Project.objects.create(name='Test Project', creator=self.user)
        self.interface = ApiInterface.objects.create(
            name='Test API', type='http', method='GET',
            url='http://example.com/api', project=self.project,
            created_by=self.user,
        )
        self.testcase = ApiTestCase.objects.create(
            name='Test Case', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            name='Step 1', order=1,
            interface_data={'method': 'GET', 'url': 'http://example.com/api'},
            testcase=self.testcase, origin_interface=self.interface,
        )

    def test_create_sync_config(self):
        config = ApiSyncConfig.objects.create(
            name='Sync Config',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method', 'url'],
            created_by=self.user,
        )
        self.assertEqual(config.name, 'Sync Config')
        self.assertTrue(config.sync_enabled)
        self.assertEqual(config.sync_mode, 'manual')

    def test_str_representation(self):
        config = ApiSyncConfig.objects.create(
            name='My Sync',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        self.assertIn('My Sync', str(config))

    def test_unique_together(self):
        ApiSyncConfig.objects.create(
            name='Config 1',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        with self.assertRaises(Exception):
            ApiSyncConfig.objects.create(
                name='Config 2',
                interface=self.interface,
                testcase=self.testcase,
                step=self.step,
                sync_fields=['url'],
                created_by=self.user,
            )

    def test_cascade_delete_interface(self):
        ApiSyncConfig.objects.create(
            name='To Delete',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        self.interface.delete()
        self.assertEqual(ApiSyncConfig.objects.count(), 0)

    def test_set_null_on_user_delete(self):
        config = ApiSyncConfig.objects.create(
            name='Orphan Config',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        self.user.delete()
        config.refresh_from_db()
        self.assertIsNone(config.created_by)

    def test_sync_mode_choices(self):
        config = ApiSyncConfig.objects.create(
            name='Auto Sync',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            sync_mode='auto',
            created_by=self.user,
        )
        self.assertEqual(config.sync_mode, 'auto')


class ApiSyncHistoryModelTest(TestCase):
    """ApiSyncHistory model tests"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.project = Project.objects.create(name='Test Project', creator=self.user)
        self.interface = ApiInterface.objects.create(
            name='Test API', type='http', method='GET',
            url='http://example.com/api', project=self.project,
            created_by=self.user,
        )
        self.testcase = ApiTestCase.objects.create(
            name='Test Case', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            name='Step 1', order=1,
            interface_data={'method': 'GET', 'url': 'http://example.com/api'},
            testcase=self.testcase, origin_interface=self.interface,
        )
        self.sync_config = ApiSyncConfig.objects.create(
            name='Sync Config',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method', 'url'],
            created_by=self.user,
        )

    def test_create_history(self):
        history = ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method', 'url'],
            old_data={'method': 'GET'},
            new_data={'method': 'POST'},
            operator=self.user,
        )
        self.assertEqual(history.sync_type, 'manual')
        self.assertEqual(history.sync_status, 'success')

    def test_str_representation(self):
        history = ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method'],
            old_data={},
            new_data={},
            operator=self.user,
        )
        self.assertIn('Sync Config', str(history))

    def test_cascade_delete_config(self):
        ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method'],
            old_data={},
            new_data={},
            operator=self.user,
        )
        self.sync_config.delete()
        self.assertEqual(ApiSyncHistory.objects.count(), 0)

    def test_ordering(self):
        h1 = ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method'],
            old_data={},
            new_data={},
            operator=self.user,
        )
        h2 = ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='auto',
            sync_status='failed',
            sync_fields=['url'],
            old_data={},
            new_data={},
            operator=self.user,
        )
        histories = list(ApiSyncHistory.objects.all())
        self.assertEqual(histories[0], h2)  # newest first
        self.assertEqual(histories[1], h1)


class ApiGlobalSyncConfigModelTest(TestCase):
    """ApiGlobalSyncConfig model tests"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.project = Project.objects.create(name='Test Project', creator=self.user)

    def test_create_global_config(self):
        config = ApiGlobalSyncConfig.objects.create(
            name='Global Config',
            project=self.project,
            sync_fields=['method', 'url'],
            created_by=self.user,
        )
        self.assertEqual(config.name, 'Global Config')
        self.assertTrue(config.sync_enabled)
        self.assertFalse(config.is_active)
        self.assertEqual(config.sync_mode, 'manual')

    def test_str_representation(self):
        config = ApiGlobalSyncConfig.objects.create(
            name='Active Config',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        self.assertIn('Active Config', str(config))

    def test_unique_together_project_name(self):
        ApiGlobalSyncConfig.objects.create(
            name='Duplicate',
            project=self.project,
            sync_fields=['method'],
            created_by=self.user,
        )
        with self.assertRaises(Exception):
            ApiGlobalSyncConfig.objects.create(
                name='Duplicate',
                project=self.project,
                sync_fields=['url'],
                created_by=self.user,
            )

    def test_save_deactivates_others(self):
        config1 = ApiGlobalSyncConfig.objects.create(
            name='Config 1',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        config2 = ApiGlobalSyncConfig.objects.create(
            name='Config 2',
            project=self.project,
            sync_fields=['url'],
            is_active=True,
            created_by=self.user,
        )
        config1.refresh_from_db()
        self.assertFalse(config1.is_active)
        self.assertTrue(config2.is_active)

    def test_save_inactive_does_not_deactivate_others(self):
        config1 = ApiGlobalSyncConfig.objects.create(
            name='Config 1',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        ApiGlobalSyncConfig.objects.create(
            name='Config 2',
            project=self.project,
            sync_fields=['url'],
            is_active=False,
            created_by=self.user,
        )
        config1.refresh_from_db()
        self.assertTrue(config1.is_active)


class ApiSyncConfigAPITest(TestCase):
    """ApiSyncConfig API tests"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apiuser', password='testpass')
        self.project = Project.objects.create(name='API Project', creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='admin')
        _grant_all_sync_perms(self.user)
        self.client.force_authenticate(user=self.user)
        self.interface = ApiInterface.objects.create(
            name='Test API', type='http', method='GET',
            url='http://example.com/api', project=self.project,
            created_by=self.user,
        )
        self.testcase = ApiTestCase.objects.create(
            name='Test Case', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            name='Step 1', order=1,
            interface_data={'method': 'GET', 'url': 'http://example.com/api'},
            testcase=self.testcase, origin_interface=self.interface,
        )
        self.base_url = f'/api/projects/{self.project.pk}/api-sync-configs/'

    def test_list_configs(self):
        ApiSyncConfig.objects.create(
            name='Config 1',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_config(self):
        data = {
            'name': 'New Config',
            'interface': self.interface.pk,
            'testcase': self.testcase.pk,
            'step': self.step.pk,
            'sync_fields': ['method', 'url'],
        }
        response = self.client.post(self.base_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        config = ApiSyncConfig.objects.get(name='New Config')
        self.assertEqual(config.created_by, self.user)

    def test_delete_config(self):
        config = ApiSyncConfig.objects.create(
            name='To Delete',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        response = self.client.delete(f'{self.base_url}{config.pk}/')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT])

    @patch('api_sync.views.sync_interface_data')
    def test_sync_now_enabled(self, mock_task):
        mock_task.delay.return_value = MagicMock(id='task-123')
        config = ApiSyncConfig.objects.create(
            name='Sync Enabled',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            sync_enabled=True,
            created_by=self.user,
        )
        response = self.client.post(f'{self.base_url}{config.pk}/sync_now/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['task_id'], 'task-123')
        self.assertFalse(response.data['used_global_config'])

    @patch('api_sync.views.sync_interface_data')
    def test_sync_now_disabled_uses_global(self, mock_task):
        mock_task.delay.return_value = MagicMock(id='task-456')
        ApiGlobalSyncConfig.objects.create(
            name='Global',
            project=self.project,
            sync_fields=['method'],
            sync_enabled=True,
            is_active=True,
            created_by=self.user,
        )
        config = ApiSyncConfig.objects.create(
            name='Sync Disabled',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            sync_enabled=False,
            created_by=self.user,
        )
        response = self.client.post(f'{self.base_url}{config.pk}/sync_now/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['used_global_config'])

    def test_sync_now_disabled_no_global(self):
        config = ApiSyncConfig.objects.create(
            name='Sync Disabled',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            sync_enabled=False,
            created_by=self.user,
        )
        response = self.client.post(f'{self.base_url}{config.pk}/sync_now/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('api_sync.views.batch_sync_interface_data')
    def test_batch_sync(self, mock_task):
        mock_task.delay.return_value = MagicMock(id='batch-task-789')
        config = ApiSyncConfig.objects.create(
            name='Batch Config',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method'],
            created_by=self.user,
        )
        data = {'config_ids': [config.pk]}
        response = self.client.post(f'{self.base_url}batch_sync/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['config_count'], 1)

    def test_batch_sync_empty_params(self):
        response = self.client.post(f'{self.base_url}batch_sync/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_access(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ApiSyncHistoryAPITest(TestCase):
    """ApiSyncHistory API tests"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apiuser', password='testpass')
        self.project = Project.objects.create(name='API Project', creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='admin')
        _grant_all_sync_perms(self.user)
        self.client.force_authenticate(user=self.user)
        self.interface = ApiInterface.objects.create(
            name='Test API', type='http', method='GET',
            url='http://example.com/api', project=self.project,
            created_by=self.user,
        )
        self.testcase = ApiTestCase.objects.create(
            name='Test Case', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            name='Step 1', order=1,
            interface_data={'method': 'GET', 'url': 'http://example.com/api'},
            testcase=self.testcase, origin_interface=self.interface,
        )
        self.sync_config = ApiSyncConfig.objects.create(
            name='Sync Config',
            interface=self.interface,
            testcase=self.testcase,
            step=self.step,
            sync_fields=['method', 'url'],
            created_by=self.user,
        )
        self.base_url = f'/api/projects/{self.project.pk}/api-sync-histories/'

    def test_list_histories(self):
        ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method'],
            old_data={'method': 'GET'},
            new_data={'method': 'POST'},
            operator=self.user,
        )
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_rollback(self):
        history = ApiSyncHistory.objects.create(
            sync_config=self.sync_config,
            sync_type='manual',
            sync_status='success',
            sync_fields=['method'],
            old_data={'method': 'GET'},
            new_data={'method': 'POST'},
            operator=self.user,
        )
        response = self.client.post(f'{self.base_url}{history.pk}/rollback/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.step.refresh_from_db()
        self.assertEqual(self.step.interface_data['method'], 'GET')

    def test_unauthenticated_access(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ApiGlobalSyncConfigAPITest(TestCase):
    """ApiGlobalSyncConfig API tests"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apiuser', password='testpass')
        self.project = Project.objects.create(name='API Project', creator=self.user)
        ProjectMember.objects.create(project=self.project, user=self.user, role='admin')
        _grant_all_sync_perms(self.user)
        self.client.force_authenticate(user=self.user)
        self.base_url = f'/api/projects/{self.project.pk}/api-global-sync-configs/'

    def test_list_configs(self):
        ApiGlobalSyncConfig.objects.create(
            name='Config 1',
            project=self.project,
            sync_fields=['method'],
            created_by=self.user,
        )
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_config(self):
        data = {
            'name': 'New Global Config',
            'sync_fields': ['method', 'url'],
            'project': self.project.pk,
        }
        response = self.client.post(self.base_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        config = ApiGlobalSyncConfig.objects.get(name='New Global Config')
        self.assertEqual(config.project, self.project)
        self.assertEqual(config.created_by, self.user)

    def test_set_active(self):
        config = ApiGlobalSyncConfig.objects.create(
            name='To Activate',
            project=self.project,
            sync_fields=['method'],
            is_active=False,
            created_by=self.user,
        )
        response = self.client.post(f'{self.base_url}{config.pk}/set_active/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config.refresh_from_db()
        self.assertTrue(config.is_active)

    def test_set_active_deactivates_others(self):
        config1 = ApiGlobalSyncConfig.objects.create(
            name='Config 1',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        config2 = ApiGlobalSyncConfig.objects.create(
            name='Config 2',
            project=self.project,
            sync_fields=['url'],
            is_active=False,
            created_by=self.user,
        )
        self.client.post(f'{self.base_url}{config2.pk}/set_active/')
        config1.refresh_from_db()
        config2.refresh_from_db()
        self.assertFalse(config1.is_active)
        self.assertTrue(config2.is_active)

    def test_set_active_already_active(self):
        config = ApiGlobalSyncConfig.objects.create(
            name='Already Active',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        response = self.client.post(f'{self.base_url}{config.pk}/set_active/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_current_config(self):
        ApiGlobalSyncConfig.objects.create(
            name='Active Config',
            project=self.project,
            sync_fields=['method'],
            is_active=True,
            created_by=self.user,
        )
        response = self.client.get(f'{self.base_url}current_config/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Active Config')

    def test_current_config_none(self):
        response = self.client.get(f'{self.base_url}current_config/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_config(self):
        config = ApiGlobalSyncConfig.objects.create(
            name='To Delete',
            project=self.project,
            sync_fields=['method'],
            created_by=self.user,
        )
        response = self.client.delete(f'{self.base_url}{config.pk}/')
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT])

    def test_unauthenticated_access(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.base_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ApiSyncPermissionTest(TestCase):
    """ApiSync permission tests"""

    def setUp(self):
        self.client = APIClient()
        self.user_a = User.objects.create_user(username='user_a', password='testpass')
        self.project_a = Project.objects.create(name='Project A', creator=self.user_a)
        ProjectMember.objects.create(project=self.project_a, user=self.user_a, role='member')

        self.outsider = User.objects.create_user(username='outsider', password='testpass')
        self.superuser = User.objects.create_superuser(username='admin', password='adminpass')

    def test_non_member_denied_sync_configs(self):
        self.client.force_authenticate(user=self.outsider)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-sync-configs/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_member_denied_global_configs(self):
        self.client.force_authenticate(user=self.outsider)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-global-sync-configs/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_allowed_sync_configs(self):
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-sync-configs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_allowed_global_configs(self):
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(f'/api/projects/{self.project_a.pk}/api-global-sync-configs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ApiSyncIsolationTest(TestCase):
    """同步模块项目隔离测试"""

    def setUp(self):
        self.client = APIClient()
        self.user_a = User.objects.create_user(username='user_a', password='testpass')
        _grant_all_sync_perms(self.user_a)
        self.project_a = Project.objects.create(name='Project A', creator=self.user_a)
        ProjectMember.objects.create(project=self.project_a, user=self.user_a, role='admin')

        self.user_b = User.objects.create_user(username='user_b', password='testpass')
        self.project_b = Project.objects.create(name='Project B', creator=self.user_b)
        ProjectMember.objects.create(project=self.project_b, user=self.user_b, role='admin')

        # Setup project A data
        self.iface_a = ApiInterface.objects.create(
            name='A API', type='http', method='GET', url='http://a.com/api',
            project=self.project_a, created_by=self.user_a,
        )
        self.tc_a = ApiTestCase.objects.create(
            name='TC A', project=self.project_a, created_by=self.user_a,
        )
        self.step_a = ApiTestCaseStep.objects.create(
            testcase=self.tc_a, name='Step A', order=1,
            origin_interface=self.iface_a,
            interface_data=self.iface_a.get_interface_data(),
        )
        self.sync_config_a = ApiSyncConfig.objects.create(
            name='Sync A', interface=self.iface_a,
            testcase=self.tc_a, step=self.step_a,
            sync_fields=['method', 'url'], sync_enabled=False,
            created_by=self.user_a,
        )

        # Setup project B data
        self.iface_b = ApiInterface.objects.create(
            name='B API', type='http', method='POST', url='http://b.com/api',
            project=self.project_b, created_by=self.user_b,
        )
        self.tc_b = ApiTestCase.objects.create(
            name='TC B', project=self.project_b, created_by=self.user_b,
        )
        self.step_b = ApiTestCaseStep.objects.create(
            testcase=self.tc_b, name='Step B', order=1,
            origin_interface=self.iface_b,
            interface_data=self.iface_b.get_interface_data(),
        )
        self.sync_config_b = ApiSyncConfig.objects.create(
            name='Sync B', interface=self.iface_b,
            testcase=self.tc_b, step=self.step_b,
            sync_fields=['method'], sync_enabled=True,
            created_by=self.user_b,
        )

        self.client.force_authenticate(user=self.user_a)
        self.sync_url = f'/api/projects/{self.project_a.pk}/api-sync-configs/'

    @patch('api_sync.views.sync_interface_data')
    def test_sync_now_global_fallback_scoped(self, mock_task):
        """sync_now 配置禁用且全局配置只查本项目 → 应返回 400"""
        # project_b has no global config for project_a
        ApiGlobalSyncConfig.objects.create(
            name='B Global', project=self.project_b,
            sync_fields=['method'], sync_enabled=True,
            is_active=True, created_by=self.user_b,
        )
        response = self.client.post(
            f'{self.sync_url}{self.sync_config_a.pk}/sync_now/',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('api_sync.views.batch_sync_interface_data')
    def test_batch_sync_cross_project_config(self, mock_task):
        """batch_sync 传入跨项目的 config_ids 应返回 400"""
        response = self.client.post(
            f'{self.sync_url}batch_sync/',
            {'config_ids': [self.sync_config_b.pk]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()


class ApiSyncTaskTest(TestCase):
    """Celery 同步任务测试"""

    def setUp(self):
        self.user = User.objects.create_user(username='taskuser', password='testpass')
        self.project = Project.objects.create(name='Task Project', creator=self.user)
        self.interface = ApiInterface.objects.create(
            name='Sync API', type='http', method='GET',
            url='http://example.com/api',
            headers={'Content-Type': 'application/json'},
            params={}, body={},
            setup_hooks=[], teardown_hooks=[],
            variables=[], validators=[], extract=[],
            project=self.project, created_by=self.user,
        )
        self.testcase = ApiTestCase.objects.create(
            name='Sync TC', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            testcase=self.testcase, name='Step 1', order=1,
            origin_interface=self.interface,
            interface_data={
                'method': 'GET', 'url': 'http://old.com/api',
                'headers': {}, 'params': {}, 'body': {},
                'setup_hooks': [], 'teardown_hooks': [],
                'variables': [], 'validators': [], 'extract': [],
            },
        )
        self.sync_config = ApiSyncConfig.objects.create(
            name='Test Sync', interface=self.interface,
            testcase=self.testcase, step=self.step,
            sync_fields=['method', 'url'], sync_enabled=True,
            created_by=self.user,
        )

    def test_sync_interface_data_with_config(self):
        """有 config 时正常同步，创建 history"""
        from api_sync.tasks import sync_interface_data
        result = sync_interface_data(self.sync_config.pk, self.user.pk)
        self.assertEqual(result['status'], 'success')
        self.assertIn('history_id', result)
        # Verify step data updated
        self.step.refresh_from_db()
        self.assertEqual(self.step.interface_data['url'], 'http://example.com/api')

    def test_sync_interface_data_global_fallback(self):
        """无 config 时使用全局配置"""
        from api_sync.tasks import sync_interface_data
        ApiGlobalSyncConfig.objects.create(
            name='Global', project=self.project,
            sync_fields=['method', 'url'], sync_enabled=True,
            is_active=True, created_by=self.user,
        )
        result = sync_interface_data(
            None, self.user.pk,
            interface_id=self.interface.pk,
            step_id=self.step.pk,
        )
        self.assertEqual(result['status'], 'success')

    def test_sync_interface_data_no_config(self):
        """无任何配置时返回错误"""
        from api_sync.tasks import sync_interface_data
        result = sync_interface_data(None, self.user.pk)
        self.assertEqual(result['status'], 'error')

    def test_sync_preserves_sync_type(self):
        """sync_type 参数正确传递到 history"""
        from api_sync.tasks import sync_interface_data
        result = sync_interface_data(
            self.sync_config.pk, self.user.pk, sync_type='manual',
        )
        self.assertEqual(result['status'], 'success')
        history = ApiSyncHistory.objects.get(id=result['history_id'])
        self.assertEqual(history.sync_type, 'manual')

    def test_batch_sync_aggregates_results(self):
        """批量同步统计 success/fail 计数"""
        from api_sync.tasks import batch_sync_interface_data
        result = batch_sync_interface_data(
            [self.sync_config.pk], self.user.pk,
        )
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['success_count'], 1)
        self.assertEqual(result['failed_count'], 0)

    def test_batch_sync_with_interface_step_pairs(self):
        """通过 interface_step_pairs 同步"""
        from api_sync.tasks import batch_sync_interface_data
        ApiGlobalSyncConfig.objects.create(
            name='Global', project=self.project,
            sync_fields=['method', 'url'], sync_enabled=True,
            is_active=True, created_by=self.user,
        )
        result = batch_sync_interface_data(
            [], self.user.pk,
            interface_step_pairs=[
                {'interface_id': self.interface.pk, 'step_id': self.step.pk},
            ],
        )
        self.assertEqual(result['success_count'], 1)

    def test_sync_nonexistent_interface(self):
        """同步不存在的 interface 返回错误"""
        from api_sync.tasks import sync_interface_data
        result = sync_interface_data(
            None, self.user.pk, interface_id=999999, step_id=self.step.pk,
        )
        self.assertEqual(result['status'], 'error')

    def test_sync_disabled_config(self):
        """禁用的 config 返回错误"""
        from api_sync.tasks import sync_interface_data
        self.sync_config.sync_enabled = False
        self.sync_config.save()
        result = sync_interface_data(self.sync_config.pk, self.user.pk)
        self.assertEqual(result['status'], 'error')


class ApiSyncModelTest(TestCase):
    """ApiSync 模型层补充测试"""

    def setUp(self):
        self.user = User.objects.create_user(username='syncmodel', password='testpass')
        self.project = Project.objects.create(name='Sync Model Project', creator=self.user)

    def test_global_config_save_deactivates_others(self):
        """新建 active 配置自动停用同项目其他配置"""
        from api_sync.models import ApiGlobalSyncConfig
        gc1 = ApiGlobalSyncConfig.objects.create(
            name='Config A', project=self.project,
            sync_fields=['method'], is_active=True,
            created_by=self.user,
        )
        gc2 = ApiGlobalSyncConfig.objects.create(
            name='Config B', project=self.project,
            sync_fields=['url'], is_active=True,
            created_by=self.user,
        )
        gc1.refresh_from_db()
        self.assertFalse(gc1.is_active)
        self.assertTrue(gc2.is_active)

    def test_global_config_inactive_does_not_deactivate(self):
        """保存 is_active=False 不影响其他配置"""
        from api_sync.models import ApiGlobalSyncConfig
        gc1 = ApiGlobalSyncConfig.objects.create(
            name='Active', project=self.project,
            sync_fields=['method'], is_active=True,
            created_by=self.user,
        )
        ApiGlobalSyncConfig.objects.create(
            name='Inactive', project=self.project,
            sync_fields=['url'], is_active=False,
            created_by=self.user,
        )
        gc1.refresh_from_db()
        self.assertTrue(gc1.is_active)

    def test_sync_config_unique_together(self):
        """interface+testcase+step 唯一约束"""
        from api_sync.models import ApiSyncConfig
        from api_interfaces.models import ApiInterface
        from api_testcases.models import ApiTestCase, ApiTestCaseStep

        interface = ApiInterface.objects.create(
            name='API', type='http', method='GET', url='/api',
            project=self.project, created_by=self.user,
        )
        tc = ApiTestCase.objects.create(
            name='TC', project=self.project, created_by=self.user,
        )
        step = ApiTestCaseStep.objects.create(
            testcase=tc, name='S1', order=1,
            interface_data={'method': 'GET', 'url': '/api'},
            origin_interface=interface,
        )
        ApiSyncConfig.objects.create(
            name='SC1', interface=interface, testcase=tc, step=step,
            sync_fields=['method'], created_by=self.user,
        )
        with self.assertRaises(Exception):
            ApiSyncConfig.objects.create(
                name='SC2', interface=interface, testcase=tc, step=step,
                sync_fields=['url'], created_by=self.user,
            )

    def test_sync_history_project_property(self):
        """history.project 通过 sync_config 返回"""
        from api_sync.models import ApiSyncConfig, ApiSyncHistory
        from api_interfaces.models import ApiInterface
        from api_testcases.models import ApiTestCase, ApiTestCaseStep

        interface = ApiInterface.objects.create(
            name='API', type='http', method='GET', url='/api',
            project=self.project, created_by=self.user,
        )
        tc = ApiTestCase.objects.create(
            name='TC', project=self.project, created_by=self.user,
        )
        step = ApiTestCaseStep.objects.create(
            testcase=tc, name='S1', order=1,
            interface_data={'method': 'GET', 'url': '/api'},
        )
        config = ApiSyncConfig.objects.create(
            name='SC', interface=interface, testcase=tc, step=step,
            sync_fields=['method'], created_by=self.user,
        )
        history = ApiSyncHistory.objects.create(
            sync_config=config, sync_type='manual', sync_status='success',
            sync_fields=['method'], old_data={}, new_data={},
            operator=self.user,
        )
        self.assertEqual(history.project, self.project)


class ApiSyncSerializerValidationTest(TestCase):
    """ApiSync serializer 验证测试"""

    def setUp(self):
        from api_sync.models import ApiSyncConfig
        from api_interfaces.models import ApiInterface
        from api_testcases.models import ApiTestCase, ApiTestCaseStep

        self.user = User.objects.create_user(username='syncseruser', password='testpass')
        self.project = Project.objects.create(name='SyncSer Project', creator=self.user)

        self.interface = ApiInterface.objects.create(
            name='API', type='http', method='GET', url='/api',
            project=self.project, created_by=self.user,
        )
        self.tc = ApiTestCase.objects.create(
            name='TC', project=self.project, created_by=self.user,
        )
        self.step = ApiTestCaseStep.objects.create(
            testcase=self.tc, name='S1', order=1,
            interface_data={'method': 'GET', 'url': '/api'},
            origin_interface=self.interface,
        )

    def test_invalid_sync_fields_rejected(self):
        """sync_fields 含无效字段 → ValidationError"""
        from api_sync.serializers import ApiGlobalSyncConfigSerializer
        serializer = ApiGlobalSyncConfigSerializer(data={
            'name': 'Test', 'sync_fields': ['invalid_field'],
            'project': self.project.pk,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('sync_fields', serializer.errors)

    def test_valid_sync_fields_accepted(self):
        """有效 sync_fields 通过验证"""
        from api_sync.serializers import ApiGlobalSyncConfigSerializer
        serializer = ApiGlobalSyncConfigSerializer(data={
            'name': 'Valid', 'sync_fields': ['method', 'url', 'headers'],
            'project': self.project.pk,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
