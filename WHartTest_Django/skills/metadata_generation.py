"""Skill Hub 的分类与短简介生成。"""
from __future__ import annotations

import json
import re

from django.core.exceptions import ValidationError


def _json_object(text: str) -> dict:
    text = str(text or '').strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start:
            raise ValidationError('模型未返回可解析的分类结果')
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValidationError('模型未返回可解析的分类结果') from exc
    if not isinstance(value, dict):
        raise ValidationError('模型返回的分类结果格式错误')
    return value


def generate_skill_metadata(*, name: str, content: str) -> dict:
    """使用平台当前激活的 LLM 生成建议，结果必须由人确认后才入库。"""
    from knowledge_evolution.capability_registry import SKILL_STAGE_OPTIONS, STAGE_LABELS
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance

    config = LLMConfig.objects.filter(is_active=True).first()
    if config is None:
        raise ValidationError('未配置可用的平台 LLM，无法自动生成分类与简介')

    choices = '\n'.join(f'- {key}: {STAGE_LABELS.get(key, key)}' for key in SKILL_STAGE_OPTIONS)
    prompt = f"""
你是测试平台 Skill Hub 的元数据编辑。根据 Skill 名称和内容，仅返回 JSON：
{{"category":"下面允许的标识符之一","description":"不超过60个中文字的正式功能简介"}}
不得返回 Markdown，不得创造新分类。

允许的分类：
{choices}

分类口径：
- 前八类是**业务能力阶段**，只在 Skill 明确服务于该测试阶段时选。
- `platform_base`（平台基础能力）：**跨阶段的公共手段**。平台自带的测试管理工具、
  浏览器自动化、视觉识别、知识库检索、URL 解析、画图之类，被多个阶段共用、
  不专属某一阶段时选它，不要硬塞进某个业务阶段。

Skill 名称：{name[:255]}
Skill 内容：
{content[:16000]}
""".strip()
    try:
        response = create_llm_instance(config, temperature=0).invoke(prompt)
    except Exception as exc:
        raise ValidationError(f'自动生成失败：{exc}') from exc
    result = _json_object(getattr(response, 'content', response))
    category = str(result.get('category') or '').strip()
    description = re.sub(r'\s+', ' ', str(result.get('description') or '')).strip()
    if category not in SKILL_STAGE_OPTIONS:
        raise ValidationError('模型返回了不受支持的 Skill 分类')
    if not description:
        raise ValidationError('模型未生成功能简介')
    return {
        'category': category,
        'category_label': STAGE_LABELS.get(category, category),
        'description': description[:60],
    }
