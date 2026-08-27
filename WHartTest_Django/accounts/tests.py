import base64
import json
import secrets
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from types import SimpleNamespace

from django.db.utils import OperationalError
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from accounts.serializers import ContentTypeSerializer
from accounts.views import MyTokenObtainPairView
from accounts.login_crypto import _private_key, public_key_payload


class EncryptedLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='encrypted-user', password='Secret123!')

    def _encrypted_credentials(self, nonce=None):
        # Redis 缓存不会随 Django 测试数据库清理；每个测试使用独立 nonce，
        # 仅重放测试复用同一份已生成密文。
        nonce = nonce or secrets.token_hex(16)
        key_response = self.client.get('/api/auth/login-key/')
        key_data = key_response.json()['data']
        public_key = serialization.load_pem_public_key(key_data['public_key'].encode('ascii'))
        plaintext = json.dumps({
            'username': self.user.username,
            'password': 'Secret123!',
            'timestamp': int(time.time() * 1000),
            'nonce': nonce,
        }).encode('utf-8')
        ciphertext = public_key.encrypt(
            plaintext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return {
            'encrypted_payload': base64.b64encode(ciphertext).decode('ascii'),
            'key_id': key_data['key_id'],
        }

    def test_encrypted_credentials_can_obtain_token(self):
        response = self.client.post('/api/token/', self._encrypted_credentials(), content_type='application/json')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('access', response.json()['data'])

    def test_encrypted_credentials_cannot_be_replayed(self):
        credentials = self._encrypted_credentials()
        first_response = self.client.post('/api/token/', credentials, content_type='application/json')

        self.assertEqual(
            first_response.status_code,
            200,
            first_response.content,
        )
        response = self.client.post('/api/token/', credentials, content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('已使用', str(response.json()))

    def test_jsencrypt_password_field_can_obtain_token(self):
        key_response = self.client.get('/api/auth/login-key/')
        key_data = key_response.json()['data']
        public_key = serialization.load_pem_public_key(key_data['public_key'].encode('ascii'))
        ciphertext = public_key.encrypt(b'Secret123!', padding.PKCS1v15())

        response = self.client.post(
            '/api/token/',
            {
                'username': self.user.username,
                'password': base64.b64encode(ciphertext).decode('ascii'),
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('access', response.json()['data'])

    def test_plaintext_password_remains_compatible(self):
        response = self.client.post(
            '/api/token/',
            {'username': self.user.username, 'password': 'Secret123!'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)

    def test_runtime_private_key_is_shared_across_worker_caches(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            runtime_key_path = Path(temp_directory) / 'login_rsa_private_key.pem'
            with patch('accounts.login_crypto._RUNTIME_PRIVATE_KEY_PATH', runtime_key_path):
                _private_key.cache_clear()
                first_worker_payload = public_key_payload()

                # 清空进程内缓存，模拟另一个 Uvicorn worker 独立加载密钥。
                _private_key.cache_clear()
                second_worker_payload = public_key_payload()

        self.assertEqual(first_worker_payload['key_id'], second_worker_payload['key_id'])
        self.assertEqual(first_worker_payload['public_key'], second_worker_payload['public_key'])


class MyTokenObtainPairViewTests(SimpleTestCase):
    def setUp(self):
        # 构造 DRF 请求工厂，模拟 token 登录请求。
        self.factory = APIRequestFactory()

    def test_returns_503_when_database_not_ready(self):
        # 模拟数据库尚未就绪时的登录请求，验证接口返回 503 而不是 500 traceback。
        request = self.factory.post(
            '/api/token/',
            {'username': 'tester', 'password': 'secret'},
            format='json'
        )

        # 条件：认证流程抛出 OperationalError；动作：调用视图；结果：返回友好错误提示。
        with patch(
            'accounts.views.BaseTokenObtainPairView.post',
            side_effect=OperationalError('database is not ready')
        ):
            response = MyTokenObtainPairView.as_view()(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['detail'], '认证服务正在启动，请稍后重试。')


class ContentTypeSerializerMenuGroupingTests(SimpleTestCase):
    def setUp(self):
        self.serializer = ContentTypeSerializer()

    def test_api_interfaces_are_grouped_under_api_testing_menu(self):
        content_type = SimpleNamespace(app_label='api_interfaces', model='apiinterface')

        self.assertEqual(self.serializer.get_app_label_cn(content_type), '接口自动化')
        self.assertEqual(self.serializer.get_app_label_subcategory(content_type), '接口管理')
        self.assertEqual(self.serializer.get_app_label_sort(content_type), 3)

    def test_task_center_is_grouped_as_top_level_task_center_menu(self):
        content_type = SimpleNamespace(app_label='task_center', model='scheduledtask')

        self.assertEqual(self.serializer.get_app_label_cn(content_type), '任务中心')
        self.assertEqual(self.serializer.get_app_label_subcategory(content_type), '任务调度')
        self.assertEqual(self.serializer.get_app_label_sort(content_type), 5)

    def test_django_celery_beat_is_grouped_under_task_center(self):
        content_type = SimpleNamespace(app_label='django_celery_beat', model='periodictask')

        self.assertEqual(self.serializer.get_app_label_cn(content_type), '任务中心')
        self.assertEqual(self.serializer.get_app_label_subcategory(content_type), '任务调度')
