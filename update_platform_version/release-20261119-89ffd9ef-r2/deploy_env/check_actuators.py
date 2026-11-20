"""Validate online actuator IDs through the rendered HTTP API (stdlib only)."""
import json
import sys
import time
import urllib.request


def parse_items(payload):
    # The renderer wraps the view's {status, data: {items}} once more.
    # Also accept a single wrapper for versions using a unified view response.
    for _ in range(3):
        if not isinstance(payload, dict):
            break
        if payload.get('status') not in (None, 'success'):
            raise ValueError('执行器列表接口返回非成功状态')
        if payload.get('code') is not None and payload['code'] != 200:
            raise ValueError('执行器列表接口返回非成功业务码')
        if 'items' in payload:
            items = payload['items']
            if not isinstance(items, list) or any(
                not isinstance(item, dict) or not item.get('id')
                or not isinstance(item.get('is_open'), bool)
                for item in items
            ):
                raise ValueError('执行器列表items结构异常，缺少id或在线状态')
            return items
        payload = payload.get('data')
    raise ValueError('执行器列表响应缺少items（已检查统一响应包装）')


def verify(expected, fetch, attempts=36, pause=time.sleep):
    expected = set(expected)
    if not expected:
        raise ValueError('未提供待验证执行器ID')
    last_error = '尚未获取执行器列表'
    for attempt in range(attempts):
        try:
            items = parse_items(fetch())
            online = {str(item['id']) for item in items if item['is_open']}
            if expected <= online:
                print('执行器已通过WebSocket连接后端：', sorted(expected))
                return
            last_error = '未在线ID=' + str(sorted(expected - online))
        except (ValueError, OSError) as exc:
            last_error = type(exc).__name__ + ': ' + str(exc)
        if attempt + 1 < attempts:
            pause(5)
    raise RuntimeError('执行器在线验收失败：' + last_error)


def fetch():
    with urllib.request.urlopen(
        'http://127.0.0.1:8000/api/ui-automation/actuators/list_actuators/', timeout=10
    ) as response:
        return json.load(response)


if __name__ == '__main__':
    verify(json.loads(sys.argv[1]), fetch)
