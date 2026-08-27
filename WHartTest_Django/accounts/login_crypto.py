"""登录凭据的 RSA-OAEP 加解密。

生产环境可通过 LOGIN_RSA_PRIVATE_KEY 为所有 Web worker 配置同一份 PEM 私钥。
未配置时在运行时临时目录原子生成密钥文件，供同一容器内的 worker 共享。
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.cache import cache


class LoginCredentialError(ValueError):
    pass


_RUNTIME_PRIVATE_KEY_PATH = (
    Path(tempfile.gettempdir()) / "wharttest" / "login_rsa_private_key.pem"
)


def _runtime_private_key():
    """加载或原子创建容器运行期共享私钥，避免多 worker 各用一把密钥。"""
    try:
        pem = _RUNTIME_PRIVATE_KEY_PATH.read_bytes()
    except FileNotFoundError:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _RUNTIME_PRIVATE_KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            file_descriptor = os.open(
                _RUNTIME_PRIVATE_KEY_PATH,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # 另一个 worker 已抢先创建，读取它生成的共享密钥。
            pem = _RUNTIME_PRIVATE_KEY_PATH.read_bytes()
        else:
            with os.fdopen(file_descriptor, "wb") as key_file:
                key_file.write(pem)

    return serialization.load_pem_private_key(pem, password=None)


@lru_cache(maxsize=1)
def _private_key():
    configured_key = getattr(settings, "LOGIN_RSA_PRIVATE_KEY", "")
    if configured_key:
        return serialization.load_pem_private_key(
            configured_key.replace("\\n", "\n").encode("utf-8"), password=None
        )
    return _runtime_private_key()


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
        # JSEncrypt 使用 PKCS#1 v1.5；保留 algorithm 字段供旧版 OAEP 客户端使用。
        "password_padding": "PKCS1_v1_5",
    }


def is_encrypted_password(value: str) -> bool:
    """判断是否为 JSEncrypt 生成的 2048-bit RSA Base64 密文。"""
    if not isinstance(value, str) or len(value) < 100:
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 256
    except (ValueError, TypeError):
        return False


def decrypt_password(ciphertext: str):
    """解密 JSEncrypt 密码字段，失败时返回 None。"""
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
        return _private_key().decrypt(encrypted, padding.PKCS1v15()).decode("utf-8")
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


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
