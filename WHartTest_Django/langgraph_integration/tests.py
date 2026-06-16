from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework.test import APIClient


class LLMConfigDeepSeekTests(TestCase):
	def setUp(self):
		self.user_model = get_user_model()
		self.user = self.user_model.objects.create_user(
			username="tester",
			password="password123",
		)
		self.client = APIClient()
		self.client.force_authenticate(self.user)

	def test_provider_choices_endpoint_includes_deepseek(self):
		response = self.client.get(reverse("provider_choices_api"))

		self.assertEqual(response.status_code, 200)
		provider_values = [
			item["value"] for item in response.data["data"]["choices"]
		]
		self.assertIn("deepseek", provider_values)

	@patch("langgraph_integration.deepseek_chat_model.ReasoningCompatibleChatDeepSeek")
	def test_create_llm_instance_uses_chatdeepseek_for_deepseek_provider(
		self, mock_chat_deepseek
	):
		from .views import create_llm_instance

		active_config = Mock()
		active_config.name = "deepseek-chat"
		active_config.provider = "deepseek"
		active_config.api_url = "https://api.deepseek.com"
		active_config.api_key = "deepseek-key"
		active_config.request_timeout = 90
		active_config.max_retries = 2

		llm = create_llm_instance(active_config, temperature=0.2)

		self.assertEqual(llm, mock_chat_deepseek.return_value)
		mock_chat_deepseek.assert_called_once_with(
			model="deepseek-chat",
			temperature=0.2,
			timeout=90,
			max_retries=2,
			api_key="deepseek-key",
			api_base="https://api.deepseek.com/v1",
		)

	def test_reasoning_compatible_chatdeepseek_round_trips_reasoning_content(self):
		from .deepseek_chat_model import ReasoningCompatibleChatDeepSeek

		model = ReasoningCompatibleChatDeepSeek(
			model="deepseek-chat",
			api_key="deepseek-key",
			api_base="https://api.deepseek.com/v1",
		)

		payload = model._get_request_payload(
			[
				HumanMessage(content="你好"),
				AIMessage(
					content="这是回答",
					additional_kwargs={"reasoning_content": "这是推理内容"},
				),
			]
		)

		self.assertEqual(payload["messages"][1]["role"], "assistant")
		self.assertEqual(
			payload["messages"][1]["reasoning_content"],
			"这是推理内容",
		)
