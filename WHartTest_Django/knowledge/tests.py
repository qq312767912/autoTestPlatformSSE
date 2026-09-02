from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import KnowledgeGlobalConfig
from .langgraph_integration import create_knowledge_tool
from .views import _mask_secret


class MultiKnowledgeBaseToolTests(SimpleTestCase):
    @patch("knowledge.langgraph_integration.KnowledgeBaseService.multi_kb_search")
    def test_multiple_knowledge_bases_use_fused_search(self, mock_search):
        mock_search.return_value = [
            {
                "content": "联合检索结果",
                "similarity_score": 0.9,
                "metadata": {"source": "requirements.docx"},
            }
        ]
        tool = create_knowledge_tool(["kb-1", "kb-2", "kb-1"], user=None)

        result = tool.invoke({"query": "查询入参"})

        self.assertIn("联合检索结果", result)
        mock_search.assert_called_once_with(
            "查询入参", ["kb-1", "kb-2"], k=5, score_threshold=0.5,
            candidate_k=10,
        )

    @patch("knowledge.langgraph_integration.KnowledgeBaseService.multi_kb_search")
    def test_coverage_priority_uses_larger_candidate_pool(self, mock_search):
        mock_search.return_value = [
            {"content": f"结果{i}", "similarity_score": 0.9 - i * 0.01, "metadata": {}}
            for i in range(20)
        ]
        tool = create_knowledge_tool(
            ["kb-1", "kb-2"], user=None, similarity_threshold=0.2,
            top_k=20, coverage_priority=True,
        )

        result = tool.invoke({"query": "生成完整测试用例"})

        self.assertIn("结果19", result)
        mock_search.assert_called_once_with(
            "生成完整测试用例", ["kb-1", "kb-2"], k=20,
            score_threshold=0.2, candidate_k=40,
        )


class KnowledgeGlobalConfigSecretHandlingTests(TestCase):
    """验证全局配置中的脱敏密钥不会在保存或测试时被破坏。"""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="testpass123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin)
        self.config = KnowledgeGlobalConfig.get_config()
        self.config.embedding_service = "custom"
        self.config.api_base_url = "https://integrate.api.nvidia.com/v1/embeddings"
        self.config.api_key = "nvapi-real-secret-NvRS"
        self.config.model_name = "baai/bge-m3"
        self.config.reranker_service = "custom"
        self.config.reranker_api_url = "https://reranker.example.com/v1/rerank"
        self.config.reranker_api_key = "reranker-real-secret"
        self.config.reranker_model_name = "Qwen3-VL-Reranker-2B"
        self.config.save()

    def test_put_global_config_keeps_real_secret_when_api_key_is_omitted(self):
        payload = {
            "embedding_service": "custom",
            "api_base_url": "https://integrate.api.nvidia.com/v1/embeddings",
            "model_name": "baai/bge-m3",
            "reranker_service": "custom",
            "reranker_api_url": "https://reranker.example.com/v1/rerank",
            "reranker_model_name": "Qwen3-VL-Reranker-2B",
            "chunk_size": 1200,
            "chunk_overlap": 150,
        }

        update_response = self.client.put(
            "/api/knowledge/global-config/", payload, format="json"
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        self.config.refresh_from_db()
        self.assertEqual(self.config.api_key, "nvapi-real-secret-NvRS")
        self.assertEqual(self.config.reranker_api_key, "reranker-real-secret")
        self.assertEqual(self.config.chunk_size, 1200)
        self.assertEqual(self.config.chunk_overlap, 150)

    @patch("requests.Session.post")
    def test_embedding_connection_uses_stored_secret_when_api_key_is_omitted(
        self, mock_post
    ):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/knowledge/test-embedding-connection/",
            {
                "embedding_service": "custom",
                "api_base_url": "https://integrate.api.nvidia.com/v1/embeddings",
                "model_name": "baai/bge-m3",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["success"], True)
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer nvapi-real-secret-NvRS",
        )

    def test_put_global_config_keeps_real_secret_when_masked_value_is_sent_back(self):
        response = self.client.get("/api/knowledge/global-config/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payload = response.json()
        payload["chunk_size"] = 1300
        payload["chunk_overlap"] = 160

        update_response = self.client.put(
            "/api/knowledge/global-config/", payload, format="json"
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        self.config.refresh_from_db()
        self.assertEqual(self.config.api_key, "nvapi-real-secret-NvRS")
        self.assertEqual(self.config.reranker_api_key, "reranker-real-secret")
        self.assertEqual(self.config.chunk_size, 1300)
        self.assertEqual(self.config.chunk_overlap, 160)
