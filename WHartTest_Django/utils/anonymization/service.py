"""
文档脱敏服务

封装 Microsoft Presidio 的 AnalyzerEngine 和 AnonymizerEngine，
提供 PII 检测预览和脱敏执行能力，供 knowledge / requirements 模块复用。
支持两种规则模式：
  1. 预设PII类型（正则匹配，后台存储，用户勾选）
  2. 自定义关键词（精确匹配，用户填明文，系统自动转正则）
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from presidio_analyzer import AnalyzerEngine, RecognizerResult, Pattern, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from .chinese_recognizers import get_all_chinese_recognizers
logger = logging.getLogger(__name__)

# 支持的 PII 实体类型（中英文标签映射）
ENTITY_LABELS: Dict[str, str] = {
    "PHONE_NUMBER": "手机号",
    "ID_CARD": "身份证号",
    "BANK_CARD": "银行卡号",
    "EMAIL_ADDRESS": "邮箱地址",
    "LICENSE_PLATE": "车牌号",
    "URL": "链接地址",
    "PERSON": "人名",
    "CREDIT_CARD": "信用卡号",
    "IP_ADDRESS": "IP地址",
}


@dataclass
class AnonymizeResult:
    """脱敏执行结果"""

    anonymized_text: str  # 脱敏后的文本
    entities_found: List[Dict[str, Any]]  # 检测到的实体列表
    operators_used: List[str]  # 使用的脱敏算子


class DocumentAnonymizer:
    """
    文档脱敏器

    用法示例::

        anonymizer = DocumentAnonymizer()

        # 预览检测到的 PII
        preview = anonymizer.analyze("联系张三，电话13800138000")
        for item in preview:
            print(item)

        # 执行脱敏
        result = anonymizer.anonymize("联系张三，电话13800138000")
        print(result.anonymized_text)
    """

    def __init__(self, language: str = "zh"):
        self._language = language
        self._analyzer = self._create_analyzer()
        self._anonymizer = AnonymizerEngine()
        self._register_recognizers()

    @staticmethod
    def _create_analyzer() -> AnalyzerEngine:
        """创建分析引擎，不加载重型 spaCy NLP 模型（400MB+）。
        所有中文 PII 识别器均基于正则，无需 NLP 模型。"""
        from presidio_analyzer.nlp_engine import NlpArtifacts

        class _LightweightNlpEngine:
            """轻量 NLP 引擎：返回空 NlpArtifacts，仅支持正则识别器，无需下载 spaCy 模型"""

            def _make_artifacts(self, text, language):
                """构建空 NlpArtifacts，不依赖 spaCy"""
                from presidio_analyzer.nlp_engine.nlp_artifacts import NlpArtifacts
                # NlpArtifacts 需要 spaCy Doc/Span，但正则识别器不用它们
                # 用最小代价构造：tokens/lemmas 用列表代替
                artifacts = object.__new__(NlpArtifacts)
                artifacts.entities = []
                artifacts.tokens = []
                artifacts.tokens_indices = []
                artifacts.lemmas = text.split() if text else []
                artifacts.nlp_engine = self
                artifacts.language = language
                artifacts.scores = []
                return artifacts

            def process_text(self, text, language):
                return self._make_artifacts(text, language)

            def process_batch(self, texts, language, batch_size=1, n_process=1, **kwargs):
                for t in texts:
                    yield t, self.process_text(t, language)

            def is_loaded(self): return True
            def load(self): pass
            def is_stopword(self, token, language): return False
            def is_punct(self, token, language): return False
            def get_supported_languages(self): return ["zh", "en"]
            def get_supported_entities(self): return []
            def get_nlp_engine_configuration_as_dict(self): return {}

        engine = AnalyzerEngine(nlp_engine=_LightweightNlpEngine())
        # 移除依赖 spaCy 模型的识别器，只保留正则类识别器
        engine.registry.recognizers = [
            r for r in engine.registry.recognizers
            if r.name != 'SpacyRecognizer'
        ]
        return engine

    def _register_recognizers(self):
        """注册识别器到分析引擎：优先加载数据库规则，回退到内置默认规则"""
        registry = self._analyzer.registry
        db_rules = self._load_db_rules()

        if db_rules:
            # 使用数据库中的动态规则
            for rule in db_rules:
                try:
                    pattern = Pattern(name=rule['name'], regex=rule['regex'], score=rule['score'])
                    recognizer = PatternRecognizer(
                        name=f"db_{rule['name']}",
                        supported_entity=rule['entity_type'],
                        supported_language="zh",
                        patterns=[pattern],
                    )
                    registry.add_recognizer(recognizer)
                    # 动态更新标签映射
                    ENTITY_LABELS[rule['entity_type']] = rule['entity_label']
                except Exception as e:
                    logger.warning("跳过无效规则 %s: %s", rule['name'], e)
            logger.info("已从数据库加载 %d 条脱敏规则", len(db_rules))
        else:
            # 回退到内置默认规则
            for recognizer in get_all_chinese_recognizers():
                registry.add_recognizer(recognizer)
            logger.info("数据库无规则，使用 %d 个内置识别器", len(get_all_chinese_recognizers()))

    @staticmethod
    def _load_db_rules() -> List[Dict[str, Any]]:
        """从数据库加载已启用的脱敏规则"""
        try:
            from operation_logs.models import AnonymizationRule
            rules = AnonymizationRule.objects.filter(is_active=True).values(
                'name', 'entity_type', 'entity_label', 'regex', 'score'
            )
            return list(rules)
        except Exception as e:
            logger.debug("加载数据库脱敏规则失败（可能尚未迁移）: %s", e)
            return []

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def analyze(
        self,
        text: str,
        entities: Optional[List[str]] = None,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        预览检测到的 PII（不修改文本）

        :param text: 待分析文本
        :param entities: 仅检测指定实体类型，如 ["PHONE_NUMBER", "ID_CARD"]
        :param language: 语言代码，默认使用初始化时的语言
        :return: 检测结果列表，每项包含 entity_type, start, end, score, text_snippet
        """
        lang = language or self._language
        results: List[RecognizerResult] = self._analyzer.analyze(
            text=text,
            language=lang,
            entities=entities,
        )

        return [
            {
                "entity_type": r.entity_type,
                "entity_label": ENTITY_LABELS.get(r.entity_type, r.entity_type),
                "start": r.start,
                "end": r.end,
                "score": round(r.score, 3),
                "text_snippet": text[r.start : r.end],
            }
            for r in sorted(results, key=lambda x: x.start)
        ]

    def anonymize(
        self,
        text: str,
        entities: Optional[List[str]] = None,
        language: Optional[str] = None,
        operator: str = "replace",
    ) -> AnonymizeResult:
        """
        执行脱敏

        :param text: 待脱敏文本
        :param entities: 仅脱敏指定实体类型，None 表示全部
        :param language: 语言代码
        :param operator: 脱敏算子，目前固定为 replace（占位符替换）
        :return: AnonymizeResult
        """
        lang = language or self._language

        # 1. 分析
        analyzer_results: List[RecognizerResult] = self._analyzer.analyze(
            text=text,
            language=lang,
            entities=entities,
        )

        # 2. 构造脱敏后的实体列表（用占位符替换）
        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=analyzer_results,
            operators={
                "DEFAULT": OperatorConfig("replace", {"new_value": ""}),
            },
        )

        # 3. 汇总检测结果
        entities_found = [
            {
                "entity_type": r.entity_type,
                "entity_label": ENTITY_LABELS.get(r.entity_type, r.entity_type),
                "start": r.start,
                "end": r.end,
                "score": round(r.score, 3),
                "text_snippet": text[r.start : r.end],
            }
            for r in sorted(analyzer_results, key=lambda x: x.start)
        ]

        return AnonymizeResult(
            anonymized_text=anonymized.text,
            entities_found=entities_found,
            operators_used=[operator],
        )

    # ------------------------------------------------------------------
    # 按配置脱敏（用于文档级规则配置）
    # ------------------------------------------------------------------

    @classmethod
    def anonymize_with_config(
        cls,
        text: str,
        enabled_preset_types: List[str],
        custom_keywords: List,
        language: str = "zh",
    ) -> AnonymizeResult:
        """
        按指定配置执行脱敏（不依赖全局规则）

        :param text: 待脱敏文本
        :param enabled_preset_types: 启用的预设敏感信息类型列表，如 ['PHONE_NUMBER', 'EMAIL_ADDRESS']
        :param custom_keywords: 自定义关键词列表，支持两种格式：
            新格式: [{'keyword': '张三', 'replacement': '某某'}, ...]
            旧格式: ['张三', '北京科技有限公司']（兼容，统一替换为 <关键词>）
        :param language: 语言代码
        :return: AnonymizeResult
        """
        instance = cls.__new__(cls)
        instance._language = language
        instance._analyzer = instance._create_analyzer()
        instance._anonymizer = AnonymizerEngine()

        registry = instance._analyzer.registry

        # 1. 注册选中的预设类型（从 DB 查找正则）
        if enabled_preset_types:
            db_rules = cls._load_db_rules()
            rule_map = {r['entity_type']: r for r in db_rules}

            for entity_type in enabled_preset_types:
                rule = rule_map.get(entity_type)
                if rule:
                    pattern = Pattern(name=rule['name'], regex=rule['regex'], score=rule['score'])
                    recognizer = PatternRecognizer(
                        name=f"preset_{rule['name']}",
                        supported_entity=rule['entity_type'],
                        supported_language="zh",
                        patterns=[pattern],
                    )
                    registry.add_recognizer(recognizer)
                    ENTITY_LABELS[rule['entity_type']] = rule['entity_label']
                else:
                    # 回退到内置识别器
                    for builtin in get_all_chinese_recognizers():
                        if builtin.supported_entities and entity_type in builtin.supported_entities:
                            registry.add_recognizer(builtin)
                            break

        # 2. 规范化自定义关键词。关键词不注册到 Presidio：Presidio 会把相邻的
        # 同类型命中合并成一个大区间，造成多个关键词及其中间内容被整体替换。
        normalized_keywords = []
        if custom_keywords:
            for item in custom_keywords:
                if isinstance(item, dict):
                    kw = (item.get('keyword') or '').strip()
                    replacement = (item.get('replacement') or '').strip() or '<关键词>'
                    if kw:
                        normalized_keywords.append({'keyword': kw, 'replacement': replacement})
                elif isinstance(item, str):
                    kw = item.strip()
                    if kw:
                        normalized_keywords.append({'keyword': kw, 'replacement': '<关键词>'})

        # 3. 先执行预设 PII 脱敏。
        lang = language or instance._language
        if enabled_preset_types:
            analyzer_results = instance._analyzer.analyze(text=text, language=lang)
            operators = {"DEFAULT": OperatorConfig("replace", {"new_value": ""})}
            anonymized = instance._anonymizer.anonymize(
                text=text, analyzer_results=analyzer_results, operators=operators,
            )
            anonymized_text = anonymized.text
        else:
            analyzer_results = []
            anonymized_text = text

        entities_found = [
            {
                "entity_type": r.entity_type,
                "entity_label": ENTITY_LABELS.get(r.entity_type, r.entity_type),
                "start": r.start,
                "end": r.end,
                "score": round(r.score, 3),
                "text_snippet": text[r.start:r.end],
            }
            for r in sorted(analyzer_results, key=lambda x: x.start)
        ]

        # 4. 自定义关键词使用一次性字面量正则替换：不区分大小写、不解释正则
        # 特殊字符，并且不会再次扫描替换后的文本。
        if normalized_keywords:
            replacements = {}
            display_keywords = {}
            for item in normalized_keywords:
                key = item['keyword'].casefold()
                replacements.setdefault(key, item['replacement'])
                display_keywords.setdefault(key, item['keyword'])

            keyword_pattern = re.compile(
                '|'.join(
                    re.escape(display_keywords[key])
                    for key in sorted(display_keywords, key=lambda value: len(display_keywords[value]), reverse=True)
                ),
                flags=re.IGNORECASE,
            )
            for match in keyword_pattern.finditer(text):
                key = match.group(0).casefold()
                entities_found.append({
                    'entity_type': f"KEYWORD_{key}",
                    'entity_label': f"关键词({display_keywords[key]})",
                    'start': match.start(),
                    'end': match.end(),
                    'score': 1.0,
                    'text_snippet': match.group(0),
                })
            anonymized_text = keyword_pattern.sub(
                lambda match: replacements[match.group(0).casefold()],
                anonymized_text,
            )
            entities_found.sort(key=lambda item: item['start'])

        return AnonymizeResult(
            anonymized_text=anonymized_text,
            entities_found=entities_found,
            operators_used=["replace"],
        )
