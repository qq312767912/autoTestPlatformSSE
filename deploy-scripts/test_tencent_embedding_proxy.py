import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import tencent_embedding_proxy as proxy


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ConfigStub:
    endpoint = "http://adp.test/atomic"
    host = "adp.test"
    secret_id = "id"
    secret_key = "key"
    model = "sn-large-multi-language-v0.2.5"
    online = False
    text_type = "document"
    service = "lkeap"
    version = "2024-05-22"
    region = "ap-guangzhou"
    token = ""
    timeout = 10


class ProxyTests(unittest.TestCase):
    @patch.object(proxy, "urlopen")
    def test_translates_adp_response_to_openai_format(self, mock_open):
        mock_open.return_value = FakeResponse(
            {"Response": {"Data": [{"Embedding": [0.1, 0.2]}], "Usage": {"TotalTokens": 3}}}
        )
        result = proxy.translate_embeddings(ConfigStub(), {"input": "你好", "model": "ignored"})
        self.assertEqual(result["data"][0]["embedding"], [0.1, 0.2])
        self.assertEqual(result["usage"]["total_tokens"], 3)
        # Python 3.7 的 unittest.mock 尚不支持 call_args.args 属性。
        request = mock_open.call_args[0][0]
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "Model": "sn-large-multi-language-v0.2.5", "Inputs": ["你好"], "Online": False
        })
        self.assertEqual(request.headers["X-tc-action"], "GetEmbedding")
        self.assertTrue(request.headers["Authorization"].startswith("TC3-HMAC-SHA256 "))

    @patch.object(proxy, "call_adp")
    def test_splits_more_than_seven_inputs(self, mock_call):
        mock_call.side_effect = [([[float(i)] for i in range(7)], 7), ([[7.0]], 1)]
        result = proxy.translate_embeddings(ConfigStub(), {"input": [str(i) for i in range(8)]})
        self.assertEqual(mock_call.call_count, 2)
        self.assertEqual(len(result["data"]), 8)
        self.assertEqual(result["usage"]["total_tokens"], 8)

    def test_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "input must"):
            proxy.translate_embeddings(ConfigStub(), {"input": []})


if __name__ == "__main__":
    unittest.main()
