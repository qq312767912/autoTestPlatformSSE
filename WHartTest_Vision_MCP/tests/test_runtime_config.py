import json

from vision_mcp.server import _client


def test_runtime_config_overrides_environment(monkeypatch):
    monkeypatch.setenv("VISION_MCP_API_KEY", "environment-secret")
    client = _client(json.dumps({
        "base_url": "http://internal-model.example/v1",
        "api_key": "database-secret",
        "model": "internal-vision",
        "chat_completions_path": "/chat/completions",
        "timeout_seconds": 30,
        "max_retries": 1,
    }))
    try:
        assert client.base_url == "http://internal-model.example/v1"
        assert client.api_key == "database-secret"
        assert client.model == "internal-vision"
        assert client.max_retries == 1
    finally:
        client.close()
