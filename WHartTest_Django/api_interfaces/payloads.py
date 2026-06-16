import json
from typing import Any


BODY_TYPES = {'none', 'form-data', 'x-www-form-urlencoded', 'raw', 'binary'}


def stringify_pair_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def normalize_key_value_pairs(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{field_name} must be a list or object.') from exc

    if isinstance(value, dict):
        return [
            {
                'key': str(key),
                'value': stringify_pair_value(item),
                'description': '',
                'enabled': True,
            }
            for key, item in value.items()
        ]

    if not isinstance(value, list):
        raise ValueError(f'{field_name} must be a list or object.')

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f'{field_name}[{index}] must be an object.')

        key = item.get('key')
        if key in (None, ''):
            raise ValueError(f'{field_name}[{index}].key is required.')

        normalized.append({
            'key': str(key),
            'value': stringify_pair_value(item.get('value', '')),
            'description': stringify_pair_value(item.get('description', '')),
            'enabled': bool(item.get('enabled', True)),
        })

    return normalized


def flatten_key_value_pairs(value: Any) -> dict[str, Any]:
    if value is None:
        return {}

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}

    if isinstance(value, dict):
        return value

    if not isinstance(value, list):
        return {}

    flattened: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        if not item.get('enabled', True):
            continue
        key = item.get('key')
        if key in (None, ''):
            continue
        flattened[str(key)] = item.get('value', '')

    return flattened


def normalize_request_body(value: Any) -> dict[str, Any]:
    if value is None:
        return {'type': 'none', 'content': None}

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {'type': 'none', 'content': None}
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {'type': 'raw', 'content': value}

    if isinstance(value, dict):
        if 'type' in value:
            body_type = value.get('type') or 'none'
            if body_type not in BODY_TYPES:
                raise ValueError(
                    f"body.type must be one of: {', '.join(sorted(BODY_TYPES))}."
                )

            if body_type == 'none':
                return {'type': 'none', 'content': None}

            content = value.get('content')
            if body_type in {'form-data', 'x-www-form-urlencoded'}:
                return {
                    'type': body_type,
                    'content': normalize_key_value_pairs(content, 'body.content'),
                }

            return {'type': body_type, 'content': content}

        if not value:
            return {'type': 'none', 'content': None}

        return {'type': 'raw', 'content': value}

    if isinstance(value, list):
        return {'type': 'raw', 'content': value}

    return {'type': 'raw', 'content': value}


def prepare_request_body_for_runner(value: Any) -> Any:
    body = normalize_request_body(value)
    body_type = body['type']
    content = body['content']

    if body_type == 'none':
        return None

    if body_type == 'raw':
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
        return content

    if body_type in {'form-data', 'x-www-form-urlencoded'}:
        return flatten_key_value_pairs(content)

    return content
