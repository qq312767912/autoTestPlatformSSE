from vision_mcp.vision_client import VisionClient


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}]}


def test_client_uses_configured_model_and_endpoint(monkeypatch):
    client = VisionClient("https://example.test/api/v4", model="glm-test", chat_completions_path="/chat/completions", max_retries=0)
    captured = {}

    def post(url, headers, json):
        captured.update(url=url, headers=headers, body=json)
        return Response()

    monkeypatch.setattr(client._client, "post", post)
    assert client.chat("hello") == "{}"
    assert captured["url"] == "https://example.test/api/v4/chat/completions"
    assert captured["body"]["model"] == "glm-test"
    client.close()
