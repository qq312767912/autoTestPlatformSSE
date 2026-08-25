from django.contrib.auth.models import Permission, User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import VisionModelConfig


class VisionModelConfigTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("vision-admin", password="test-pass")
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="mcp_tools",
            content_type__model="visionmodelconfig",
        ))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_api_key_is_encrypted_and_never_returned(self):
        response = self.client.post("/api/mcp_tools/vision-model-configs/", {
            "name": "内网视觉模型",
            "base_url": "http://vision.example/v1",
            "chat_completions_path": "/chat/completions",
            "model": "vision-model",
            "api_key": "secret-value",
            "timeout_seconds": 120,
            "max_retries": 2,
            "is_active": True,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("api_key", response.data)
        self.assertTrue(response.data["has_api_key"])

        config = VisionModelConfig.objects.get()
        self.assertNotIn("secret-value", config.encrypted_api_key)
        self.assertEqual(config.get_api_key(), "secret-value")

        detail = self.client.get(f"/api/mcp_tools/vision-model-configs/{config.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("api_key", detail.data)

    def test_blank_api_key_keeps_existing_secret_on_patch(self):
        config = VisionModelConfig(
            base_url="http://vision.example/v1", model="vision-model"
        )
        config.set_api_key("original-secret")
        config.save()

        response = self.client.patch(
            f"/api/mcp_tools/vision-model-configs/{config.pk}/",
            {"model": "vision-model-v2", "api_key": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.get_api_key(), "original-secret")
