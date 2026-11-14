"""平台 UI 生成浏览器；按任务隔离，无需切换所选 Skill。"""
import hashlib
import json
import os
import subprocess
import shutil
import tempfile
import threading
from pathlib import Path
from .auth_state_binding import prepare_storage_state

_lock = threading.Lock()
_sessions = set()
ALLOWED = {'open', 'snapshot', 'click', 'dblclick', 'fill', 'type', 'press', 'hover', 'check', 'uncheck', 'select', 'scroll', 'get', 'eval', 'back', 'forward', 'reload', 'close'}


def run_ui_browser(binding, command, arguments):
    if command not in ALLOWED or not isinstance(arguments, list) or any(not isinstance(x, str) for x in arguments):
        raise ValueError('不支持的浏览器操作')
    if any(x.startswith('--') for x in arguments):
        raise ValueError('操作参数不能包含浏览器启动选项')
    if command == 'close' and arguments:
        raise ValueError('只能关闭当前任务浏览器')
    session_id = binding.session.session_id
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    key = 'ui-gen-' + digest
    env = os.environ.copy()
    env['AGENT_BROWSER_IDLE_TIMEOUT_MS'] = '900000'
    env['AGENT_BROWSER_EXECUTABLE_PATH'] = (os.environ.get('AGENT_BROWSER_EXECUTABLE_PATH') or os.environ.get('RECORDER_BROWSER_EXECUTABLE_PATH') or shutil.which('chromium') or shutil.which('chromium-browser') or '')
    if not env['AGENT_BROWSER_EXECUTABLE_PATH']:
        raise ValueError('未配置平台浏览器可执行文件')
    env['AGENT_BROWSER_MAX_OUTPUT'] = '24000'
    env.pop('AGENT_BROWSER_PROFILE', None)
    env.pop('AGENT_BROWSER_SESSION_NAME', None)
    env.pop('AGENT_BROWSER_STATE', None)
    env.pop('AGENT_BROWSER_AUTO_CONNECT', None)
    env.pop('AGENT_BROWSER_CDP', None)
    state = None
    secrets = []
    auth_id = binding.generation_context.get('auth_state_id')
    if auth_id:
        from ui_automation.models import UiAuthState
        auth = UiAuthState.objects.get(pk=auth_id, env_config_id=binding.generation_context['env_config_id'], is_active=True)
        state = prepare_storage_state(auth.state_json)
        session_storage = auth.state_json.get('session_storage', [])
        if not isinstance(session_storage, list):
            raise ValueError('sessionStorage 格式无效')
        for cookie in state.get('cookies', []):
            if cookie.get('value'): secrets.append(cookie['value'])
        for origin in state.get('origins', []) + session_storage:
            for item in origin.get('localStorage', []) + origin.get('sessionStorage', []):
                if item.get('value'): secrets.append(item['value'])
    else:
        session_storage = []
    # Keep initialization and commands serial; a second call cannot replace another task's context.
    with _lock:
        with tempfile.TemporaryDirectory(prefix='ui-generation-') as root:
            args = ['agent-browser', '--session', key, '--json']
            # Rehydrate state on each worker's first open; restart cannot silently discard auth.
            if command == 'open' and (key not in _sessions):
                subprocess.run(['agent-browser','--session',key,'close'], env=env, capture_output=True, timeout=15)
                if state:
                    path = Path(root)/'state.json';path.write_text(json.dumps(state));path.chmod(0o600)
                    args += ['--state', str(path)]
                if session_storage:
                    path = Path(root)/'session.js'
                    path.write_text('for (const o of '+json.dumps(session_storage)+') { if (location.origin === o.origin) { for (const v of (o.sessionStorage || [])) sessionStorage.setItem(v.name,v.value); } }')
                    path.chmod(0o600);args += ['--init-script',str(path)]
            elif key not in _sessions and command != 'close':
                raise ValueError('请先 open 起始 URL 初始化任务浏览器')
            result = subprocess.run(args+[command]+arguments, env=env, text=True, capture_output=True, timeout=90)
            output = (result.stdout or '') + (result.stderr or '')
            for secret in sorted(set(secrets),key=len,reverse=True):
                output = output.replace(secret, '[REDACTED]')
            if result.returncode:
                raise ValueError(output[:24000])
            if command == 'close': _sessions.discard(key)
            else: _sessions.add(key)
            return output[:24000]
