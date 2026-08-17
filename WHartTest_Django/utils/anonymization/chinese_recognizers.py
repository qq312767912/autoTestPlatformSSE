"""
中文 PII 识别器

为 Presidio AnalyzerEngine 注册的中文敏感信息识别器，
覆盖手机号、身份证号、银行卡号、车牌号等中国大陆常见 PII 模式。
"""

import re
from typing import List, Optional

from presidio_analyzer import Pattern, PatternRecognizer


class ChinesePhoneRecognizer(PatternRecognizer):
    """中国大陆手机号识别器（1[3-9]开头的11位数字）"""

    PATTERNS = [
        Pattern(
            name="chinese_phone",
            regex=r"(?<!\d)1[3-9]\d{9}(?!\d)",
            score=0.85,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="PHONE_NUMBER",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


class ChineseIDCardRecognizer(PatternRecognizer):
    """中国大陆18位身份证号识别器"""

    PATTERNS = [
        Pattern(
            name="chinese_id_card",
            regex=r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)",
            score=0.85,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="ID_CARD",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


class ChineseBankCardRecognizer(PatternRecognizer):
    """中国大陆银行卡号识别器（16-19位数字）"""

    PATTERNS = [
        Pattern(
            name="chinese_bank_card",
            regex=r"(?<!\d)6[0-9]{15,18}(?!\d)",
            score=0.70,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="BANK_CARD",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


class ChineseLicensePlateRecognizer(PatternRecognizer):
    """中国大陆车牌号识别器（普通车牌 + 新能源车牌）"""

    PATTERNS = [
        Pattern(
            name="chinese_license_plate",
            regex=r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁][A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]",
            score=0.75,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="LICENSE_PLATE",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


class ChineseEmailRecognizer(PatternRecognizer):
    """邮箱地址识别器（增强版，覆盖中文语境下常见的邮箱格式）"""

    PATTERNS = [
        Pattern(
            name="chinese_email",
            regex=r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            score=0.80,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


class ChineseURLRecognizer(PatternRecognizer):
    """URL 链接识别器"""

    PATTERNS = [
        Pattern(
            name="chinese_url",
            regex=r"https?://[^\s<>\"')\]]+",
            score=0.75,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="URL",
            supported_language="zh",
            patterns=self.PATTERNS,
        )


def get_all_chinese_recognizers():
    """返回所有中文 PII 识别器实例列表"""
    return [
        ChinesePhoneRecognizer(),
        ChineseIDCardRecognizer(),
        ChineseBankCardRecognizer(),
        ChineseLicensePlateRecognizer(),
        ChineseEmailRecognizer(),
        ChineseURLRecognizer(),
    ]
