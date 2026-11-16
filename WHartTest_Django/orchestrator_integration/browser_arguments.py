"""平台浏览器仅接受业务页面操作，不允许启动选项和本地文件导航。"""
import re
import json
import hashlib
from urllib.parse import urlsplit


def validate_browser_arguments(command, arguments):
    if not isinstance(arguments, list) or any(not isinstance(value, str) for value in arguments):
        raise ValueError('浏览器参数必须是字符串列表')
    if any(value.startswith('-') and not re.fullmatch(r'-\d+(?:\.\d+)?', value) for value in arguments):
        raise ValueError('操作参数不能包含浏览器启动选项')
    if command == 'open':
        if len(arguments) != 1:
            raise ValueError('open 必须提供一个 HTTP(S) URL')
        url = urlsplit(arguments[0])
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            raise ValueError('平台浏览器只允许无内嵌凭据的 HTTP(S) URL')
    if command == 'close' and arguments:
        raise ValueError('只能关闭当前任务浏览器')


def browser_context_signature(auth_id, state, session_storage):
    payload=json.dumps([auth_id,state,session_storage],ensure_ascii=True,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(payload.encode()).hexdigest()
