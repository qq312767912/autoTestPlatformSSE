"""生成并持久化 WS 密钥，同步本机执行器配置；不向输出或示例写入密钥。"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import tempfile


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(env_file, config):
    content = env_file.read_text() if env_file.exists() else ""
    match = re.search(r"^UI_WS_ACTUATOR_TOKEN\s*=\s*(.*?)\s*$", content, re.M)
    token = os.environ.get("UI_WS_ACTUATOR_TOKEN") or (match[1].strip("\"'") if match else "") or secrets.token_hex(32)
    if not re.fullmatch(r"[A-Za-z0-9_\-]{32,}", token):
        raise ValueError("UI_WS_ACTUATOR_TOKEN 必须为至少32位字母、数字、下划线或连字符")
    line = "UI_WS_ACTUATOR_TOKEN=" + token
    content = re.sub(r"^UI_WS_ACTUATOR_TOKEN\s*=.*$", line, content, flags=re.M) if match else content.rstrip() + "\n" + line + "\n"
    atomic_write(env_file, content)
    text = config.read_text() if config.exists() else "[server]\n"
    section = re.search(r"(?ms)^\[server\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if not section:
        raise ValueError("执行器配置缺少 [server] 段")
    body = section[1]
    line = "ws_token = " + json.dumps(token)
    body = re.sub(r"(?m)^\s*ws_token\s*=.*$", line, body) if re.search(r"(?m)^\s*ws_token\s*=", body) else body.rstrip() + "\n" + line + "\n\n"
    atomic_write(config, text[:section.start(1)] + body + text[section.end(1):])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--config", type=Path, default=Path("WHartTest_Actuator/config.toml"))
    args = parser.parse_args()
    prepare(args.env_file, args.config)
    print("WS 密钥已持久化并同步到执行器配置（未修改示例）")
