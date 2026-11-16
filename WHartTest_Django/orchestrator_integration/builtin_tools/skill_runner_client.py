"""Backend侧执行面客户端。显式runner模式不会回退；未配置时保留旧部署inprocess兼容性。

传递经校验的精确版本文件快照，仅注入用户凭据；不共享业务数据目录。
"""

from __future__ import annotations

import json
import base64
import hashlib
from pathlib import Path
import logging
import threading
from functools import wraps
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("orchestrator_integration")


class SkillRunnerUnavailable(RuntimeError):
    """runner 未配置或不可达。"""


@dataclass
class RunnerJobOutcome:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    workspace_files: list
    artifacts: list = field(default_factory=list)


def runner_exec_mode() -> str:
    """显式启用runner；旧部署未配置时沿用inprocess，错误配置拒绝执行。"""
    mode = (
        getattr(_settings(), "SKILL_EXEC_MODE", None)
        or os.environ.get("SKILL_EXEC_MODE")
        or "inprocess"
    )
    mode = str(mode).strip().lower()
    if mode not in {"runner", "inprocess"}:
        raise SkillRunnerUnavailable("SKILL_EXEC_MODE 只允许 runner 或 inprocess")
    return mode


def _settings():
    try:
        from django.conf import settings

        return settings
    except Exception:  # pragma: no cover - 独立运行时无 Django
        return None


def _setting(name: str, default=None):
    settings = _settings()
    if settings is not None:
        try:
            value = getattr(settings, name, None)
            if value not in (None, ""):
                return value
        except Exception:
            pass
    value = os.environ.get(name)
    return default if value in (None, "") else value


def _runner_endpoint() -> tuple[str, str]:
    url = str(_setting("SKILL_RUNNER_URL", "") or "").strip().rstrip("/")
    token = str(_setting("SKILL_RUNNER_TOKEN", "") or "").strip()
    if not url:
        raise SkillRunnerUnavailable(
            "SKILL_EXEC_MODE=runner 但未配置 SKILL_RUNNER_URL"
        )
    if not token:
        raise SkillRunnerUnavailable(
            "SKILL_EXEC_MODE=runner 但未配置 SKILL_RUNNER_TOKEN"
        )
    return url, token


_RUNNER_CALL_GATE = threading.BoundedSemaphore(1)


def _serialized_job(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        if not _RUNNER_CALL_GATE.acquire(timeout=150):
            raise SkillRunnerUnavailable('runner 等待队列繁忙，请稍后重试')
        try:
            return function(*args, **kwargs)
        finally:
            _RUNNER_CALL_GATE.release()
    return invoke


@_serialized_job
def submit_job(
    *,
    skill_dir: str,
    command: str,
    timeout_seconds: int,
    runtime_env: Optional[dict] = None,
    user_id: object = None,
    project_id: object = None,
    chat_session_id: object = None,
    test_case_id: object = None,
    skill_id: object = None,
    skill_name: object = None,
    skill_version_id: object = None,
    source_sha256: object = None,
) -> RunnerJobOutcome:
    """把作业提交给 runner 并等待结果。"""
    base_url, token = _runner_endpoint()
    payload = {
        "package": build_package(skill_dir),
        "skill_version_id": str(skill_version_id or ""),
        "source_sha256": str(source_sha256 or ""),
        "command": command,
        "timeout_seconds": int(timeout_seconds),
        # runtime_env 只应包含用户级 API Key 与产物目录等按会话注入的变量。
        "runtime_env": {key:value for key,value in (runtime_env or {}).items() if key in {"WHARTTEST_API_KEY", "WHARTTEST_BACKEND_URL", "PYTHONUTF8", "PYTHONIOENCODING"}},
        "user_id": user_id,
        "project_id": project_id,
        "chat_session_id": chat_session_id,
        "test_case_id": test_case_id,
        "skill_id": skill_id,
        "skill_name": skill_name,
    }
    request = urllib.request.Request(
        f"{base_url}/execute",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    # 作业超时 + 宽限：runner 侧是内核级超时，这里多给 30 秒等它把结果写完。
    http_timeout = max(30, int(timeout_seconds) + 30)
    try:
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise SkillRunnerUnavailable("runner 响应超限")
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(body.get("returncode"), int):
                raise SkillRunnerUnavailable("runner 响应格式无效")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = ""
        if exc.code == 400:
            # 策略拒绝/目录非法等业务性失败，交给上层按错误返回给模型。
            return RunnerJobOutcome(
                returncode=-1,
                stdout="",
                stderr=detail or "runner 拒绝了该命令",
                duration_ms=0,
                workspace_files=[],
            )
        raise SkillRunnerUnavailable(f"runner 返回 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SkillRunnerUnavailable(f"runner 不可达: {exc}") from exc

    return RunnerJobOutcome(
        returncode=int(body["returncode"]),
        stdout=str(body.get("stdout") or ""),
        stderr=str(body.get("stderr") or ""),
        duration_ms=int(body.get("duration_ms") or 0),
        workspace_files=list(body.get("workspace_files") or []),
        artifacts=list(body.get("artifacts") or []),
    )


def build_package(skill_dir):
    root = Path(skill_dir).resolve(strict=True)
    if not root.is_dir() or not (root / 'SKILL.md').is_file():
        raise SkillRunnerUnavailable('Skill 版本目录无效')
    files = []; total = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise SkillRunnerUnavailable('Skill 版本包不允许符号链接')
        if not path.is_file(): continue
        if any(part in {'__pycache__', '.git', 'node_modules'} for part in path.relative_to(root).parts): continue
        if len(files) >= 2000: raise SkillRunnerUnavailable('Skill 文件数量超限')
        size = path.stat().st_size
        if total + size > 24 * 1024 * 1024: raise SkillRunnerUnavailable('Skill 版本超过24MiB传输上限')
        data = path.read_bytes(); total += len(data)
        if total > 24 * 1024 * 1024: raise SkillRunnerUnavailable('Skill 版本超过24MiB传输上限')
        files.append({'name':path.relative_to(root).as_posix(), 'data_b64':base64.b64encode(data).decode(), 'sha256':hashlib.sha256(data).hexdigest()})
    return files


def restore_artifacts(artifacts, destination):
    root = Path(destination).resolve(); total = 0; seen = set()
    if not isinstance(artifacts,list) or len(artifacts)>20: raise SkillRunnerUnavailable('runner产物数量超限')
    validated=[]
    for artifact in artifacts:
        name=artifact.get('name','')
        if not name or name in seen or name.startswith('/') or '\\' in name or ':' in name or any(p in {'','.', '..'} for p in name.split('/')):
            raise SkillRunnerUnavailable('runner产物路径无效')
        seen.add(name); path=root.joinpath(*name.split('/'))
        if not path.resolve().is_relative_to(root): raise SkillRunnerUnavailable('runner产物越界')
        try: data=base64.b64decode(artifact['data_b64'],validate=True)
        except (KeyError,ValueError,TypeError) as exc: raise SkillRunnerUnavailable('runner产物编码无效') from exc
        total+=len(data)
        if total>8*1024*1024 or len(data)!=artifact.get('size') or hashlib.sha256(data).hexdigest()!=artifact.get('sha256'):
            raise SkillRunnerUnavailable('runner产物大小或指纹不匹配')
        validated.append((path,data))
    for path,data in validated:
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
