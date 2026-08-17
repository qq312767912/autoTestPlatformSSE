#!/usr/bin/env python3
"""OpenAI-compatible proxy for Tencent LKEAP/ADP GetEmbedding."""

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger("tencent-embedding-proxy")
CONTENT_TYPE = "application/json; charset=utf-8"


def env_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self):
        endpoint = os.getenv("ADP_ENDPOINT", "").strip()
        host = os.getenv("ADP_HOST", "").strip()
        if not endpoint and host:
            endpoint = f"http://{host}/atomic"
        if endpoint and "://" not in endpoint:
            endpoint = f"http://{endpoint}"
        parsed = urlsplit(endpoint)
        if not parsed.netloc:
            raise ValueError("Set ADP_ENDPOINT=http://<host>/atomic or ADP_HOST=<host>")
        self.endpoint = endpoint.rstrip("/")
        self.host = parsed.netloc
        self.secret_id = os.getenv("ADP_SECRET_ID", "MOCK_CAPI_SECRET_ID_VALUE")
        self.secret_key = os.getenv("ADP_SECRET_KEY", "MOCK_CAPI_SECRET_KEY_VALUE")
        self.model = os.getenv("ADP_MODEL", "sn-large-multi-language-v0.2.5")
        self.online = env_bool("ADP_ONLINE", False)
        self.text_type = os.getenv("ADP_TEXT_TYPE", "document")
        self.service = os.getenv("ADP_SERVICE", "lkeap")
        self.version = os.getenv("ADP_VERSION", "2024-05-22")
        self.region = os.getenv("ADP_REGION", "ap-guangzhou")
        self.token = os.getenv("ADP_TOKEN", "")
        self.timeout = float(os.getenv("ADP_TIMEOUT", "120"))
        self.proxy_api_key = os.getenv("PROXY_API_KEY", "")
        self.listen_host = os.getenv("PROXY_HOST", "0.0.0.0")
        self.listen_port = int(os.getenv("PROXY_PORT", "8920"))


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def hmac256(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def tc3_authorization(config, body, timestamp):
    canonical_headers = f"content-type:{CONTENT_TYPE}\nhost:{config.host}\n"
    canonical_request = (
        "POST\n/\n\n" + canonical_headers + "\ncontent-type;host\n"
        + sha256_hex(body.encode("utf-8"))
    )
    date = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
    scope = f"{date}/{config.service}/tc3_request"
    string_to_sign = (
        f"TC3-HMAC-SHA256\n{timestamp}\n{scope}\n"
        + sha256_hex(canonical_request.encode("utf-8"))
    )
    secret_date = hmac256(f"TC3{config.secret_key}".encode("utf-8"), date)
    secret_service = hmac256(secret_date, config.service)
    secret_signing = hmac256(secret_service, "tc3_request")
    signature = hmac256(secret_signing, string_to_sign).hex()
    return (
        f"TC3-HMAC-SHA256 Credential={config.secret_id}/{scope}, "
        f"SignedHeaders=content-type;host, Signature={signature}"
    )


def call_adp(config, inputs, requested_model=None, text_type=None):
    model = config.model or requested_model
    request_body = {"Model": model, "Inputs": inputs}
    if model.startswith("lke-text-embedding-"):
        request_body["TextType"] = text_type or config.text_type
    else:
        request_body["Online"] = config.online
    body = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
    timestamp = str(int(time.time()))
    headers = {
        "Host": config.host,
        "X-TC-Timestamp": timestamp,
        "X-TC-Version": config.version,
        "X-TC-Action": "GetEmbedding",
        "X-TC-Region": config.region,
        "Authorization": tc3_authorization(config, body, timestamp),
        "Content-Type": CONTENT_TYPE,
    }
    if config.token:
        headers["X-TC-Token"] = config.token
    request = Request(config.endpoint, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=config.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ADP HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect to ADP: {exc.reason}") from exc
    response_data = payload.get("Response", payload)
    if response_data.get("Error"):
        error = response_data["Error"]
        raise RuntimeError(f"{error.get('Code', 'ADPError')}: {error.get('Message', '')}")
    data = response_data.get("Data")
    if not isinstance(data, list) or len(data) != len(inputs):
        raise RuntimeError(f"Unexpected ADP response: {json.dumps(payload, ensure_ascii=False)[:1000]}")
    embeddings = [item.get("Embedding") for item in data]
    if any(not isinstance(vector, list) for vector in embeddings):
        raise RuntimeError("ADP response does not contain valid Embedding arrays")
    usage = response_data.get("Usage") or {}
    return embeddings, int(usage.get("TotalTokens", usage.get("TotalToken", 0)) or 0)


def translate_embeddings(config, payload):
    inputs = payload.get("input")
    if isinstance(inputs, str):
        inputs = [inputs]
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("input must be a non-empty string or string array")
    if any(not isinstance(item, str) or not item for item in inputs):
        raise ValueError("every input item must be a non-empty string")
    vectors, total_tokens = [], 0
    for start in range(0, len(inputs), 7):
        batch_vectors, batch_tokens = call_adp(
            config, inputs[start:start + 7], payload.get("model"), payload.get("text_type")
        )
        vectors.extend(batch_vectors)
        total_tokens += batch_tokens
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": config.model or payload.get("model", ""),
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TencentEmbeddingProxy/1.0"

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in {"/health", "/healthz", "/readyz"}:
            self._json(200, {"status": "ok", "upstream": self.server.config.endpoint})
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        if self.path.rstrip("/") not in {"/v1/embeddings", "/embeddings"}:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return
        config = self.server.config
        if config.proxy_api_key:
            expected = f"Bearer {config.proxy_api_key}"
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self._json(401, {"error": {"message": "invalid proxy API key", "type": "authentication_error"}})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10 * 1024 * 1024:
                raise ValueError("invalid Content-Length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._json(200, translate_embeddings(config, payload))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
        except Exception as exc:
            request_id = str(uuid.uuid4())
            LOG.exception("upstream request failed, request_id=%s", request_id)
            self._json(502, {"error": {"message": str(exc), "type": "upstream_error", "request_id": request_id}})

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config()
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    server.config = config
    LOG.info("listening on %s:%s; upstream=%s", config.listen_host, config.listen_port, config.endpoint)
    server.serve_forever()


if __name__ == "__main__":
    main()
