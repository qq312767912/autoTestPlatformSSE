from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from langgraph_integration.models import ChatSession
from projects.models import Project, ProjectMember
from ui_automation.models import UiAuthState, UiEnvironmentConfig

from orchestrator_integration.auth_state_binding import (
    AuthStateBindingError,
    bind_chat_auth_state,
    prepare_storage_state,
)


class AuthStateBindingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="llm-auth-user", password="x")
        self.other = User.objects.create_user(username="llm-auth-other", password="x")
        self.project = Project.objects.create(name="LLM Auth Project", creator=self.user)
        self.other_project = Project.objects.create(name="Other Project", creator=self.other)
        ProjectMember.objects.create(project=self.project, user=self.user, role="owner")
        ProjectMember.objects.create(project=self.other_project, user=self.other, role="owner")
        self.env = UiEnvironmentConfig.objects.create(
            project=self.project, name="星企航环境", base_url="https://star.test.sseinfo.com", creator=self.user
        )
        self.other_env = UiEnvironmentConfig.objects.create(
            project=self.other_project, name="其他环境", creator=self.other
        )
        self.state = UiAuthState.objects.create(
            name="星企航测试账号登录态",
            env_config=self.env,
            creator=self.user,
            state_json={
                "cookies": [{"name": "session", "value": "SECRET_COOKIE", "domain": ".sseinfo.com", "path": "/", "expires": -1}],
                "origins": [{"origin": "https://star.test.sseinfo.com", "localStorage": [{"name": "token", "value": "SECRET_TOKEN"}]}],
            },
        )
        self.other_state = UiAuthState.objects.create(
            name="不可见登录态", env_config=self.other_env, creator=self.other,
            state_json={"cookies": [], "origins": []},
        )

    def test_prepare_storage_state_filters_expired_cookie(self):
        state = prepare_storage_state({
            "cookies": [
                {"name": "old", "expires": 10},
                {"name": "session", "expires": -1},
                {"name": "valid", "expires": 200},
            ],
            "origins": [],
        }, now=100)
        self.assertEqual([c["name"] for c in state["cookies"]], ["session", "valid"])

    def test_cross_project_auth_state_is_rejected(self):
        with self.assertRaisesMessage(AuthStateBindingError, "不属于当前项目"):
            bind_chat_auth_state(
                user=self.user, project=self.project, session_id="cross-project",
                message="test", auth_state_provided=True, auth_state_id=self.other_state.id,
            )

    def test_explicit_id_bind_and_clear(self):
        bound = bind_chat_auth_state(
            user=self.user, project=self.project, session_id="binding",
            message="test", auth_state_provided=True, auth_state_id=self.state.id,
        )
        self.assertTrue(bound.changed)
        self.assertEqual(bound.auth_state.id, self.state.id)
        cleared = bind_chat_auth_state(
            user=self.user, project=self.project, session_id="binding",
            message="test", auth_state_provided=True, auth_state_id=None,
        )
        self.assertTrue(cleared.changed)
        self.assertIsNone(cleared.auth_state)
        self.assertIsNone(ChatSession.objects.get(session_id="binding").auth_state_id)

    def test_strict_name_binding(self):
        result = bind_chat_auth_state(
            user=self.user, project=self.project, session_id="by-name",
            message="使用「星企航测试账号登录态」登录态测试后台", auth_state_provided=False,
            auth_state_id=None,
        )
        self.assertEqual(result.auth_state.id, self.state.id)

    def test_summary_api_never_exposes_secrets_or_cross_project_state(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get(f"/api/orchestrator/auth-states/?project_id={self.project.id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rendered = response.content.decode("utf-8")
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["id"], self.state.id)
        self.assertNotIn("state_json", rendered)
        self.assertNotIn("SECRET_COOKIE", rendered)
        self.assertNotIn("SECRET_TOKEN", rendered)


class PlaywrightBindingIsolationTests(TestCase):
    def test_manager_recreates_session_when_auth_binding_changes(self):
        from unittest.mock import MagicMock, patch
        from orchestrator_integration.builtin_tools.persistent_playwright import PlaywrightSessionManager

        manager = PlaywrightSessionManager(idle_timeout_seconds=60)
        fake_proc_a = MagicMock()
        fake_proc_a.exec_run_js.return_value = "a"
        fake_proc_a.last_page_url = "about:blank"
        fake_proc_b = MagicMock()
        fake_proc_b.exec_run_js.return_value = "b"
        fake_proc_b.last_page_url = "about:blank"
        with patch(
            "orchestrator_integration.builtin_tools.persistent_playwright._PlaywrightNodeProcess",
            side_effect=[fake_proc_a, fake_proc_b],
        ):
            first = manager.execute_run_js(
                session_key="u_p_chat_tool", skill_dir="/tmp/skill", run_js_args=["1"], env={},
                auth_binding={"auth_state_id": 1, "storage_state": {"cookies": [], "origins": []}},
            )
            second = manager.execute_run_js(
                session_key="u_p_chat_tool", skill_dir="/tmp/skill", run_js_args=["2"], env={},
                auth_binding={"auth_state_id": 2, "storage_state": {"cookies": [], "origins": []}},
            )
        self.assertEqual((first, second), ("a", "b"))
        fake_proc_a.initialize_context.assert_called_once()
        fake_proc_a.terminate.assert_called_once()
        fake_proc_b.initialize_context.assert_called_once()
        manager.close_all()

    def test_login_redirect_detection(self):
        from orchestrator_integration.builtin_tools.persistent_playwright import PlaywrightSessionManager

        self.assertTrue(PlaywrightSessionManager._is_login_page(
            "https://passport.test.sseinfo.com/sso", []
        ))
        self.assertTrue(PlaywrightSessionManager._is_login_page(
            "https://example.test/custom-auth", ["/custom-auth"]
        ))
        self.assertFalse(PlaywrightSessionManager._is_login_page(
            "https://star.test.sseinfo.com/admin/", []
        ))
