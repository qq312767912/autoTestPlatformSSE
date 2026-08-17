"""登录凭据的 RSA-OAEP 加解密。

生产环境应通过 LOGIN_RSA_PRIVATE_KEY 为所有 Web worker 配置同一份 PEM 私钥。
未配置时会在当前进程生成临时密钥，仅适合单进程开发环境。
"""

import base64
import hashlib
import json
import time
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.cache import cache


class LoginCredentialError(ValueError):
    pass


@lru_cache(maxsize=1)
def _private_key():
    configured_key = getattr(settings, "LOGIN_RSA_PRIVATE_KEY", "")
    if configured_key:
        return serialization.load_pem_private_key(
            configured_key.replace("\\n", "\n").encode("utf-8"), password=None
        )
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_key_payload():
    public_pem = _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return {
        "algorithm": "RSA-OAEP-256",
        "public_key": public_pem,
        "key_id": hashlib.sha256(public_pem.encode("ascii")).hexdigest()[:16],
        "expires_in": 300,
    }


def decrypt_credentials(ciphertext: str, key_id: str):
    expected_key_id = public_key_payload()["key_id"]
    if key_id != expected_key_id:
        raise LoginCredentialError("登录加密密钥已更新，请重试。")

    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
        plaintext = _private_key().decrypt(
            encrypted,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        payload = json.loads(plaintext.decode("utf-8"))
        username = payload["username"]
        password = payload["password"]
        timestamp = int(payload["timestamp"])
        nonce = payload["nonce"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise LoginCredentialError("登录凭据密文无效。") from None

    if not isinstance(username, str) or not isinstance(password, str) or not isinstance(nonce, str):
        raise LoginCredentialError("登录凭据密文无效。")
    if abs(int(time.time() * 1000) - timestamp) > 60_000:
        raise LoginCredentialError("登录请求已过期，请重试。")
    if len(nonce) < 16 or not cache.add(f"login-nonce:{nonce}", True, timeout=120):
        raise LoginCredentialError("登录请求已使用，请重试。")

    return username, password
