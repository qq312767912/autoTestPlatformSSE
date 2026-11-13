"""登录态更新必须保留记录和绑定，不得跨环境覆盖。"""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from projects.models import Project
from ui_automation.models import UiEnvironmentConfig, UiAuthState
from ui_automation.views import UiRecorderSessionViewSet


class AuthStateUpdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="auth-update")
        self.project = Project.objects.create(name="P")
        self.env = UiEnvironmentConfig.objects.create(project=self.project, name="E", creator=self.user)
        self.state = UiAuthState.objects.create(env_config=self.env, name="保留名称", description="保留描述", state_json={"cookies": []}, creator=self.user)
        self.meta = SimpleNamespace(user_id=self.user.username, env_config_id=self.env.id, auth_state_id=self.state.id)
        self.payload = {"cookies": [], "origins": [], "session_storage": [{"origin": "https://test.local", "sessionStorage": [{"name": "token", "value": "new"}]}]}

    def save_state(self, target):
        request = APIRequestFactory().post("/", {"auth_state_id": target}, format="json")
        force_authenticate(request, user=self.user)
        session = Mock()
        session.save_login_state.return_value = self.payload
        with patch("ui_automation.recorder.session_manager.recorder_manager.get", return_value=session), patch("ui_automation.recorder.session_manager.recorder_manager.get_meta", return_value=self.meta):
            return UiRecorderSessionViewSet.as_view({"post": "save_login_state"})(request, pk="session")

    def test_session_storage_only_updates_same_record(self):
        response = self.save_state(self.state.id)
        self.assertEqual(response.status_code, 200)
        self.state.refresh_from_db()
        self.assertEqual(UiAuthState.objects.count(), 1)
        self.assertEqual(self.state.name, "保留名称")
        self.assertEqual(self.state.description, "保留描述")
        self.assertEqual(self.state.state_json, self.payload)
        self.assertEqual(self.meta.auth_state_id, self.state.id)

    def test_cannot_update_another_environment(self):
        other = UiEnvironmentConfig.objects.create(project=self.project, name="Other", creator=self.user)
        self.state.env_config = other
        self.state.save()
        self.assertEqual(self.save_state(self.state.id).status_code, 400)
        self.state.refresh_from_db()
        self.assertEqual(self.state.state_json, {"cookies": []})

    def test_invalid_target_is_rejected(self):
        self.assertEqual(self.save_state("invalid").status_code, 400)
