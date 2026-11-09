"""Strict structured-response parsing and privacy-preserving failure diagnostics."""
import hashlib
import json
import re


def response_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block if isinstance(block, str) else str(block.get('text') or '')
                       for block in content if isinstance(block, (str, dict)))
    raise TypeError('模型响应不是文本或文本块列表')


def parse_json_response(content):
    text = response_text(content).strip()
    # Only unwrap a fence around the whole response: embedded code fences are data.
    fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    if start < 0:
        raise ValueError('LLM未返回JSON对象')
    payload, end = json.JSONDecoder().raw_decode(text, start)
    if text[end:].strip():
        raise ValueError('JSON对象后存在额外内容')
    if not isinstance(payload, dict):
        raise ValueError('LLM未返回JSON对象')
    return payload


def json_failure_diagnostic(response, exc):
    content = getattr(response, 'content', response)
    try:
        text = response_text(content)
    except TypeError:
        text = str(type(content).__name__)
    metadata = getattr(response, 'response_metadata', {}) or {}
    detail = {'error_type': type(exc).__name__, 'response_length': len(text),
              'response_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'finish_reason': str(metadata.get('finish_reason') or '')[:40],
              'usage': getattr(response, 'usage_metadata', None) or {},
              'raw_response_saved': False}
    if isinstance(exc, json.JSONDecodeError):
        # The decoder's doc is the exact parsed text (possibly fence-unwrapped).
        syntax = re.sub(r'[^\s{}\[\]:,\\"\x27]', 'x', exc.doc[max(0, exc.pos-100):exc.pos+100])
        detail.update({'message': exc.msg, 'line': exc.lineno, 'column': exc.colno,
                       'position': exc.pos, 'syntax_window': syntax,
                       'cursor_in_window': min(exc.pos, 100)})
    return detail


def verification_pending(findings):
    return any(item.get('verification_status') in {'not_verified', 'verification_failed'} for item in findings)
