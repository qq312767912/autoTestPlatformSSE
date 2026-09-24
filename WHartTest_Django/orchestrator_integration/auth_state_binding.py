"""LLM 探索会话的登录态绑定、脱敏摘要与 Playwright 注入准备。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from langgraph_integration.models import ChatSession, LlmAuthStateUsage
from ui_automation.models import UiAuthState


class AuthStateBindingError(ValueError):
    pass


_EXPLICIT_NAME_PATTERNS = (
    re.compile(r"使用[「『\"](?P<name>[^」』\"]+)[」』\"]登录态"),
    re.compile(r"使用登录态[「『\"](?P<name>[^」』\"]+)[」』\"]"),
)


def _validate_state_json(value: Any) -> dict:
    if not isinstance(value, dict):
        raise AuthStateBindingError("登录态数据格式无效，请重新录制登录态")
    cookies = value.get('cookies', [])
    origins = value.get('origins', [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise AuthStateBindingError("登录态数据格式无效，请重新录制登录态")
    return value


def prepare_storage_state(value: Any, *, now: float | None = None) -> dict:
    """校验 storageState，并过滤明确过期的持久 Cookie。"""
    state = _validate_state_json(value)
    current = time.time() if now is None else now
    original = state.get('cookies', [])
    valid = []
    persistent_count = 0
    for cookie in original:
        if not isinstance(cookie, dict):
            continue
        expires = cookie.get('expires')
        if isinstance(expires, (int, float)) and expires > 0:
            persistent_count += 1
            if expires <= current:
                continue
        valid.append(dict(cookie))
    if original and persistent_count == len(original) and not valid:
        raise AuthStateBindingError("登录态 Cookie 已过期，请重新录制")
    return {'cookies': valid, 'origins': list(state.get('origins', []))}


def auth_state_summary(auth_state: UiAuthState) -> dict:
    status = 'valid'
    try:
        prepared = prepare_storage_state(auth_state.state_json)
    except AuthStateBindingError as exc:
        prepared = {'cookies': [], 'origins': []}
        status = 'expired' if '过期' in str(exc) else 'invalid'
    if not auth_state.is_active:
        status = 'disabled'
    cookie_count = len(prepared['cookies'])
    origin_count = len(prepared['origins'])
    return {
        'id': auth_state.id,
        'name': auth_state.name,
        'env_config_id': auth_state.env_config_id,
        'env_name': auth_state.env_config.name,
        'base_url': auth_state.env_config.base_url,
        'credential_summary': f'{cookie_count} Cookies，{origin_count} 个 localStorage 域',
        'status': status,
        'updated_at': auth_state.updated_at.isoformat() if auth_state.updated_at else None,
    }


def get_project_auth_states(project_id: int):
    return UiAuthState.objects.select_related('env_config').filter(
        env_config__project_id=project_id,
    ).order_by('env_config__name', '-updated_at')


def _extract_explicit_name(message: str | None) -> str | None:
    if not message:
        return None
    for pattern in _EXPLICIT_NAME_PATTERNS:
        match = pattern.search(message)
        if match:
            return match.group('name').strip()
    return None


@dataclass(frozen=True)
class BindingResult:
    session: ChatSession
    changed: bool
    auth_state: UiAuthState | None


def bind_chat_auth_state(
    *, user, project, session_id: str, message: str,
    auth_state_provided: bool, auth_state_id: Any,
) -> BindingResult:
    session, _ = ChatSession.objects.get_or_create(
        session_id=session_id,
        defaults={
            'user': user,
            'project': project,
            'title': f'新对话 - {message[:30]}',
        },
    )
    if session.user_id != user.id or session.project_id != project.id:
        raise AuthStateBindingError("对话会话不属于当前用户或项目")

    selected = None
    if auth_state_provided:
        if auth_state_id not in (None, ''):
            try:
                selected = get_project_auth_states(project.id).get(id=int(auth_state_id), is_active=True)
            except (TypeError, ValueError, UiAuthState.DoesNotExist):
                raise AuthStateBindingError("登录态不存在、未启用或不属于当前项目")
    else:
        explicit_name = _extract_explicit_name(message)
        if explicit_name:
            matches = list(get_project_auth_states(project.id).filter(name=explicit_name, is_active=True)[:2])
            if not matches:
                raise AuthStateBindingError(f"未找到登录态：{explicit_name}")
            if len(matches) > 1:
                raise AuthStateBindingError(f"登录态名称不唯一，请在选择器中指定：{explicit_name}")
            selected = matches[0]
        else:
            selected = session.auth_state

    if selected is not None:
        prepare_storage_state(selected.state_json)

    old_id = session.auth_state_id
    new_id = selected.id if selected else None
    changed = old_id != new_id
    if changed:
        session.auth_state = selected
        session.auth_state_bound_at = timezone.now() if selected else None
        session.save(update_fields=['auth_state', 'auth_state_bound_at', 'updated_at'])
    return BindingResult(session=session, changed=changed, auth_state=selected)


def get_runtime_auth_binding(
    *, user_id: int, project_id: int, chat_session_id: str,
    auth_state_id: int | None,
):
    if not auth_state_id:
        return None
    try:
        auth_state = get_project_auth_states(project_id).get(id=auth_state_id, is_active=True)
    except UiAuthState.DoesNotExist:
        raise AuthStateBindingError("绑定的登录态不存在、未启用或不属于当前项目")
    chat_session = ChatSession.objects.filter(
        session_id=chat_session_id,
        user_id=user_id,
        project_id=project_id,
        auth_state_id=auth_state.id,
    ).first()
    if not chat_session:
        raise AuthStateBindingError("对话登录态绑定已变化，请重新发起探索")
    LlmAuthStateUsage.objects.get_or_create(
        user_id=user_id,
        project_id=project_id,
        chat_session=chat_session,
        auth_state_id_snapshot=auth_state.id,
        result='started',
        ended_at__isnull=True,
        defaults={
            'auth_state_name_snapshot': auth_state.name,
            'target_origin': auth_state.env_config.base_url or '',
        },
    )
    extra_config = auth_state.env_config.extra_config or {}
    llm_auth_config = extra_config.get('llm_auth', {}) if isinstance(extra_config, dict) else {}
    login_url_patterns = llm_auth_config.get('login_url_patterns', []) if isinstance(llm_auth_config, dict) else []
    return {
        'auth_state_id': auth_state.id,
        'auth_state_name': auth_state.name,
        'storage_state': prepare_storage_state(auth_state.state_json),
        'login_url_patterns': [str(item) for item in login_url_patterns if item],
    }


def close_auth_usage(*, user_id: int, project_id: int, chat_session_id: str) -> None:
    LlmAuthStateUsage.objects.filter(
        user_id=user_id,
        project_id=project_id,
        chat_session__session_id=chat_session_id,
        result='started',
        ended_at__isnull=True,
    ).update(result='closed', ended_at=timezone.now())
