"""T23：用例审查「已确认报告 → Skill 候选版本」的自进化入口。

这一层回答一个具体问题：**人工在报告里逐条确认过的结论，怎么变成对 Skill 包的改进。**

为什么要单独有一层，而不是让人直接去 Skill Hub 改包：

- 用例审查是目前唯一的单次能力 Agent，它的改进依据不能是"AI 觉得自己哪里错了"，
  而必须是**人工复核后确认的错误**。这些确认就落在平台自己导出的报告
  （``testcases/review_service.py::_write_report`` 的「问题明细」页）里 ——
  「问题确认 / 问题描述 / 修改点 / 不采纳原因」四列是早就留好的人工位，
  本模块只负责把它们读回来。
- 派生必须走 ``SkillEvolutionService.derive_candidate``：它保证候选落在**新的不可变
  版本目录**、一个字节都不碰 active 包、落盘前做静态校验、并给出 diff 与回滚目标。
  本模块不重复实现这些安全约束，只在调用它之前把"人工确认"翻译成它要求的
  "已确认归因"。

口径（改动前先读这里，页面文案与它是同一套）：

- 人工在「问题确认」列标 **否** → 这条 AI 报出的问题在人工复核时不成立 → **误报**。
  归因类别取 ``prompt_error``：审查提示词对该类问题的判定条件放得太宽。
- 人工标 **是** 但改写了「问题描述」或「修改点」 → 结论方向对、表述不可直接执行。
  归因类别取 ``generation_error``。
- 人工标 **是** 且没有改写 → 判断与表述都被认可，**不是缺陷**，不进归因。
- 留空 → **未确认**。必须计数并报出来：人工漏填会让"这份报告已全部确认"
  变成一个没人验证的假设，而派生的护栏小节正是照着这份结论写的。

「报告打分」与平台导出口径保持一致：从报告最后一个 Sheet 读取「采纳率」，
不接受页面手工输入的分数。采纳率**只作为不同版本间对比的评分维度**，
不作为能否上传或能否进化的门槛 —— 门槛做成硬阻断，等于要求每一个中间版本
都必须一次跨过同一条线，而 skill 本来就是一点点优化出来的。
分数落 ``FeedbackEvent``（``signal='accepted'``、``value`` 存采纳率、
``reason_code='report_acceptance_rate'``），带 ``skill_version`` 绑定，
既可横向对比各版本、也可被后续评测直接读。

改进依据的来源是 **AI 提候选、人工确认**（见 ``report_optimization``）：
默认路径由 LLM 读四要素（当前 Skill 包 / 本轮缺陷 / 历史归因 / 人工确认报告）
提出候选归因，人工逐条确认后才进入派生；未配置 LLM 时降级为
"人工在报告里写下的结论直接落已确认归因"（本模块原有的那条路径）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import FeedbackEvent, GenerationOutput
from .report_parsing import (
    ACCEPTANCE_LABEL,
    DEFAULT_ACCEPTANCE_REFERENCE,
)
from .report_parsing import cell_text as _cell_text
from .report_parsing import locate_acceptance
from .report_parsing import normalize_text as _normalize
from .report_parsing import read_acceptance_from_workbook
from .trace_models import FailureAttribution

#: 报告里承载问题明细的页名。改名会让解析直接失败（而不是读错页），这是有意的：
#: 静默降级成"读第一个页"会在模板换版时把摘要页当成明细页读，产出一堆空归因。
DETAIL_SHEET_NAME = "问题明细"

#: 必须存在的列。缺任何一列都不能解析 —— 少「问题确认」就无从判断人工结论，
#: 少「问题类型」就分不出改进点。
REQUIRED_COLUMNS = ("问题类型", "问题确认")

#: 可选列。缺了只是信息少一点，不影响判定。
OPTIONAL_COLUMNS = (
    "Sheet", "行号", "用例编号/名称", "模块", "原文", "严重程度",
    "问题说明", "修改建议", "判定", "反馈结论", "问题描述", "修改点", "不采纳原因",
)

#: 「问题确认」列表示"人工认可这条问题成立"的取值。
AFFIRMATIVE_VALUES = {"是", "y", "yes", "true", "1", "成立", "采纳"}
#: 表示"这条问题是误报"的取值。
NEGATIVE_VALUES = {"否", "n", "no", "false", "0", "不成立", "不采纳"}

FEEDBACK_CONCLUSION_LABELS = {
    "accepted": "完全采纳",
    "accepted_rewritten": "采纳但需改写",
    "false_positive": "误报",
    "business_pending": "待业务确认",
    "unverifiable": "暂无法验证",
    "not_applicable": "不适用",
    "duplicate": "重复问题",
    "known_or_fixed": "已知/已修复",
    "unconfirmed": "未确认",
    "other": "其他",
}
FEEDBACK_CONCLUSION_ALIASES = {
    _normalize(label): key for key, label in FEEDBACK_CONCLUSION_LABELS.items()
}
FEEDBACK_CONCLUSION_ALIASES.update({
    "采纳": "accepted", "完全接受": "accepted",
    "采纳但需修改": "accepted_rewritten", "采纳需改写": "accepted_rewritten",
    "不成立": "false_positive", "不采纳": "false_positive",
    "待确认": "business_pending", "业务待确认": "business_pending",
    "无法验证": "unverifiable", "无法核验": "unverifiable",
    "重复": "duplicate", "已修复": "known_or_fixed", "已知问题": "known_or_fixed",
})

QUALITY_DIMENSION_LABELS = {
    "precondition": "前置条件", "steps": "操作步骤", "expected_result": "预期结果",
    "test_data": "测试数据", "environment_permission": "环境/权限",
    "platform_consistency": "平台一致性", "case_naming": "用例命名",
    "copy_quality": "文案表达", "business_rule": "业务规则", "other": "其他",
}

#: 人工认可但改写了描述/建议时，说明 AI 生成的表述不可直接执行。
CATEGORY_FALSE_ALARM = "prompt_error"
CATEGORY_REWRITTEN = "generation_error"

#: 归因类别 → 证据里记录的人工信号。
#:
#: 这两类在飞轮信号词表里恰好是正负两端（见 ``evaluators.FeedbackOutcomeEvaluator``），
#: 写错会让护栏对下次执行的模型传达相反的含义：误报记 ``false_positive``；
#: 而"结论被认可、只是表述不可直接执行"记 ``defect_confirmed``——它表达的是
#: **这条缺陷成立**，不是"这条缺陷做得好"。曾经两类共用 ``false_positive``，
#: 结果是 `generation_error` 的护栏条目上挂着"相关信号：false_positive"，
#: 读起来像是要求模型别再报这类问题，与人工的真实结论正好相反。
SIGNAL_BY_CATEGORY = {
    CATEGORY_FALSE_ALARM: "false_positive",
    CATEGORY_REWRITTEN: "defect_confirmed",
}

#: 每条归因最多带几条明细样本进证据。样本不是越全越好：``build_patch`` 会把
#: 前 3 条里带 ``feedback_signals`` 的写进包，写成几十条会把 SKILL.md 撑爆。
MAX_EVIDENCE_SAMPLES = 5

#: 采纳率的**参考线**（百分制）。名字沿用旧称以免打断既有引用，
#: 但语义已变：它是"低于此值值得看一眼"的提示线，**不是**进化的准入条件。
#: 数值与 ``report_parsing.DEFAULT_ACCEPTANCE_REFERENCE`` 同源，只有一处真值。
DEFAULT_HUMAN_SCORE_THRESHOLD = DEFAULT_ACCEPTANCE_REFERENCE


def _infer_negative_conclusion(reason: str) -> str:
    """旧报告没有「反馈结论」时，按不采纳原因做保守兼容映射。"""
    text = _normalize(reason)
    rules = (
        (("待确认", "业务确认", "需求确认", "规则未明确", "口径未明确"), "business_pending"),
        (("环境", "权限", "账号", "数据不足", "无法验证", "无法核验", "暂不具备"), "unverifiable"),
        (("不适用", "非本系统", "非本模块", "非本轮", "不在范围"), "not_applicable"),
        (("重复",), "duplicate"),
        (("已知", "已修复", "已有缺陷", "已有问题"), "known_or_fixed"),
        (("误报", "不成立", "原文正确", "原文已", "已有等价"), "false_positive"),
    )
    for keywords, conclusion in rules:
        if any(keyword in text for keyword in keywords):
            return conclusion
    return "other" if text else "false_positive"


def _quality_dimension(issue_type: str) -> str:
    """从报告问题类型归一成稳定的问题维度，保留原问题类型供细分展示。"""
    text = _normalize(issue_type)
    rules = (
        (("前置", "前提", "依赖"), "precondition"),
        (("步骤", "操作", "入口"), "steps"),
        (("预期", "断言", "验收", "结果"), "expected_result"),
        (("数据", "参数", "边界值"), "test_data"),
        (("环境", "权限", "账号"), "environment_permission"),
        (("平台", "端", "模块一致"), "platform_consistency"),
        (("名称", "命名"), "case_naming"),
        (("文案", "措辞", "表述", "错别字"), "copy_quality"),
        (("业务", "规则", "流程"), "business_rule"),
    )
    for keywords, dimension in rules:
        if any(keyword in text for keyword in keywords):
            return dimension
    return "other"


@dataclass(frozen=True)
class ConfirmedDefect:
    """一类被人工确认的缺陷（按「归因类别 + 报告问题类型」聚合）。"""

    category: str
    issue_type: str
    count: int
    samples: tuple = ()
    hypothesis: str = ""
    feedback_conclusion: str = ""
    feedback_conclusion_label: str = ""
    quality_dimension: str = "other"
    quality_dimension_label: str = "其他"

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "issue_type": self.issue_type,
            "count": self.count,
            "hypothesis": self.hypothesis,
            "feedback_conclusion": self.feedback_conclusion,
            "feedback_conclusion_label": self.feedback_conclusion_label,
            "quality_dimension": self.quality_dimension,
            "quality_dimension_label": self.quality_dimension_label,
            "samples": [dict(item) for item in self.samples],
        }


@dataclass(frozen=True)
class FeedbackGroup:
    """页面展示的人工反馈分组；它不等同于可用于 Skill 派生的技术归因。"""

    conclusion: str
    conclusion_label: str
    quality_dimension: str
    quality_dimension_label: str
    issue_type: str
    count: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ReportScan:
    """一份已确认报告的解析结果。"""

    total_rows: int = 0
    affirmative: int = 0
    negative: int = 0
    unconfirmed: int = 0
    rewritten: int = 0
    defects: tuple = ()
    feedback_groups: tuple = ()
    conclusion_counts: dict = field(default_factory=dict)
    warnings: tuple = ()
    source_name: str = ""
    acceptance_rate: float = 0.0
    acceptance_sheet: str = ""

    @property
    def acceptance_score(self) -> float:
        return round(self.acceptance_rate * 100, 2)

    @property
    def confirmed(self) -> int:
        return self.affirmative + self.negative

    @property
    def defect_total(self) -> int:
        return sum(item.count for item in self.defects)

    @property
    def defect_group_total(self) -> int:
        return len(self.defects)

    def as_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "affirmative": self.affirmative,
            "negative": self.negative,
            "rewritten": self.rewritten,
            "unconfirmed": self.unconfirmed,
            "confirmed": self.confirmed,
            "defect_total": self.defect_total,
            "defect_group_total": self.defect_group_total,
            "defects": [item.as_dict() for item in self.defects],
            "feedback_groups": [item.as_dict() for item in self.feedback_groups],
            "conclusion_counts": dict(self.conclusion_counts),
            "warnings": list(self.warnings),
            "source_name": self.source_name,
            "acceptance_rate": self.acceptance_rate,
            "acceptance_score": self.acceptance_score,
            "acceptance_sheet": self.acceptance_sheet,
        }


class CaseReviewReportParser:
    """解析平台导出的审查报告，抽出人工确认结论。"""

    @classmethod
    def parse(cls, data: bytes) -> ReportScan:
        """``data`` 是报告文件字节。解析失败一律抛 ``ValidationError``。"""
        if not data:
            raise ValidationError("报告文件为空，无法解析")
        try:
            from openpyxl import load_workbook
            from io import BytesIO

            workbook = load_workbook(BytesIO(data), data_only=True, read_only=True)
        except ValidationError:
            raise
        except Exception as exc:  # pragma: no cover - openpyxl 的异常类型不稳定
            raise ValidationError(f"报告无法作为 Excel 打开：{exc}") from exc

        if DETAIL_SHEET_NAME not in workbook.sheetnames:
            raise ValidationError(
                f"报告缺少「{DETAIL_SHEET_NAME}」页，无法确认这是一份平台导出的审查报告"
                f"（现有页：{'、'.join(workbook.sheetnames)}）"
            )
        sheet = workbook[DETAIL_SHEET_NAME]
        # 采纳率由公共件读：同一套"按标签定位、不绑坐标"的约定，
        # 四阶段报告也走它，避免两处各写一份解析。
        acceptance_value, acceptance_sheet_name = read_acceptance_from_workbook(
            workbook, required=True,
        )

        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValidationError(f"「{DETAIL_SHEET_NAME}」页是空的")
        columns = {_cell_text(name): index for index, name in enumerate(header)}
        missing = [name for name in REQUIRED_COLUMNS if name not in columns]
        if missing:
            raise ValidationError(
                f"报告「{DETAIL_SHEET_NAME}」页缺少必需列：{'、'.join(missing)}"
            )

        index_of = {
            name: columns[name] for name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS
            if name in columns
        }

        def value_of(row, name: str) -> str:
            index = index_of.get(name)
            if index is None or index >= len(row):
                return ""
            return _cell_text(row[index])

        total = affirmative = negative = rewritten = unconfirmed = 0
        buckets: dict[tuple, dict] = {}
        feedback_buckets: dict[tuple, int] = {}
        conclusion_counts = {key: 0 for key in FEEDBACK_CONCLUSION_LABELS}

        for row in rows:
            if row is None or all(_cell_text(cell) == "" for cell in row):
                continue
            total += 1

            confirmed_raw = row[index_of["问题确认"]] if index_of["问题确认"] < len(row) else ""
            signal = _normalize(confirmed_raw)
            explicit_conclusion = FEEDBACK_CONCLUSION_ALIASES.get(_normalize(value_of(row, "反馈结论")))
            has_rewrite = bool(value_of(row, "问题描述") or value_of(row, "修改点"))
            if explicit_conclusion:
                conclusion = explicit_conclusion
            elif signal in NEGATIVE_VALUES:
                conclusion = _infer_negative_conclusion(value_of(row, "不采纳原因"))
            elif signal in AFFIRMATIVE_VALUES:
                conclusion = "accepted_rewritten" if has_rewrite else "accepted"
            else:
                conclusion = "unconfirmed"

            issue_type = value_of(row, "问题类型") or "未标注类型"
            dimension = _quality_dimension(issue_type)
            conclusion_counts[conclusion] += 1
            feedback_key = (conclusion, dimension, issue_type)
            feedback_buckets[feedback_key] = feedback_buckets.get(feedback_key, 0) + 1

            if signal in NEGATIVE_VALUES:
                negative += 1
            elif signal in AFFIRMATIVE_VALUES:
                affirmative += 1
            else:
                unconfirmed += 1

            if conclusion == "accepted_rewritten":
                rewritten += 1
                category = CATEGORY_REWRITTEN
            elif conclusion == "false_positive":
                category = CATEGORY_FALSE_ALARM
            else:
                continue

            key = (category, conclusion, dimension, issue_type)
            bucket = buckets.setdefault(key, {"samples": [], "count": 0})
            bucket["count"] += 1
            if len(bucket["samples"]) < MAX_EVIDENCE_SAMPLES:
                bucket["samples"].append({
                    "sheet": value_of(row, "Sheet"),
                    "row": value_of(row, "行号"),
                    "case_id": value_of(row, "用例编号/名称"),
                    "severity": value_of(row, "严重程度"),
                    "reason": _sample_reason(
                        category,
                        reject_reason=value_of(row, "不采纳原因"),
                        description=value_of(row, "问题描述"),
                        fix=value_of(row, "修改点"),
                    ),
                })

        if acceptance_value is None:
            # 平台报告的采纳率是 Excel 公式。文件若没有公式缓存值，
            # 严格按最后页定义的“采纳 ÷ 审查提供意见”口径重算。
            acceptance_value = affirmative / total if total else 0.0
        workbook.close()

        defects = tuple(
            ConfirmedDefect(
                category=category,
                issue_type=issue_type,
                count=bucket["count"],
                samples=tuple(bucket["samples"]),
                hypothesis=cls._hypothesis(
                    category=category, issue_type=issue_type,
                    count=bucket["count"], samples=bucket["samples"],
                ),
                feedback_conclusion=conclusion,
                feedback_conclusion_label=FEEDBACK_CONCLUSION_LABELS[conclusion],
                quality_dimension=dimension,
                quality_dimension_label=QUALITY_DIMENSION_LABELS[dimension],
            )
            for (category, conclusion, dimension, issue_type), bucket in sorted(
                buckets.items(), key=lambda item: (-item[1]["count"], item[0])
            )
        )
        feedback_groups = tuple(
            FeedbackGroup(
                conclusion=conclusion,
                conclusion_label=FEEDBACK_CONCLUSION_LABELS[conclusion],
                quality_dimension=dimension,
                quality_dimension_label=QUALITY_DIMENSION_LABELS[dimension],
                issue_type=issue_type,
                count=count,
            )
            for (conclusion, dimension, issue_type), count in sorted(
                feedback_buckets.items(), key=lambda item: (-item[1], item[0])
            )
        )

        warnings = []
        if unconfirmed:
            warnings.append(
                f"有 {unconfirmed} 条问题未在「问题确认」列填写结论，已忽略；"
                "这些条目不会进入自进化依据"
            )
        return ReportScan(
            total_rows=total,
            affirmative=affirmative,
            negative=negative,
            rewritten=rewritten,
            unconfirmed=unconfirmed,
            # 解析层必须保留人工确认的全部信息。候选数量控制属于下游的
            # “分批生成 + 语义归并”，不能用切片的方式在这里丢数据。
            defects=defects,
            feedback_groups=feedback_groups,
            conclusion_counts=conclusion_counts,
            warnings=tuple(warnings),
            acceptance_rate=round(acceptance_value, 6),
            acceptance_sheet=acceptance_sheet_name,
        )

    @staticmethod
    def _acceptance_value(sheet) -> float | None:
        """兼容包装：在单个 Sheet 里按标签定位「采纳率」。

        实现已上提到 ``report_parsing.locate_acceptance`` —— 四阶段报告要复用同一套约定。
        保留本方法是因为既有测试与外部脚本可能直接调它；缺标签仍按原语义抛错。
        """
        found, value = locate_acceptance(sheet)
        if not found:
            raise ValidationError(
                f"报告最后一个 Sheet 缺少「{ACCEPTANCE_LABEL}」，"
                "请上传平台导出并完成人工确认的报告"
            )
        return value

    @classmethod
    def _hypothesis(cls, *, category: str, issue_type: str, count: int, samples: list) -> str:
        """把一组确认结论写成**祈使句式**的护栏要求。

        必须写成要求而不是"这里错了"：这句话会被 ``build_patch`` 原样写进
        SKILL.md 的受管护栏小节，交给下一次执行的模型读。写成陈述句，
        模型读到的是历史；写成要求，它读到的才是约束。
        """
        reasons = [item.get("reason") for item in samples if item.get("reason")]
        if category == CATEGORY_FALSE_ALARM:
            text = (
                f"用例审查在「{issue_type}」上出现 {count} 条误报：人工复核判定这些问题不成立。"
                f"报出该类问题前必须先自证原文中存在可核查的缺陷证据，无证据不得报出。"
            )
            if reasons:
                text += "人工给出的不采纳原因：" + "；".join(dict.fromkeys(reasons))[:400] + "。"
            return text
        text = (
            f"用例审查在「{issue_type}」上有 {count} 条问题说明或修改建议被人工改写："
            f"结论方向被认可，但表述不可直接执行。输出该类问题时按人工改写后的口径"
            f"描述问题与修改点，不要只给结论。"
        )
        if reasons:
            text += "人工改写示例：" + "；".join(dict.fromkeys(reasons))[:400] + "。"
        return text


def _sample_reason(category: str, *, reject_reason: str, description: str, fix: str) -> str:
    """按缺陷类别挑「最该写进护栏的那句话」。

    两类缺陷的人工输入偏重不同，用同一套优先级必然一头是废话：

    - 误报：人填的是「不采纳原因」，那正是"为什么这条不该报"，直接可用。
    - 说明被改写：人填的是「修改点」（改成什么样）。若先取「问题描述」，
      常常只是把问题类型重抄一遍，写进护栏等于没说。
    """
    if category == CATEGORY_FALSE_ALARM:
        return reject_reason or description or fix
    return fix or description or reject_reason


def _attribution_fingerprint(review_id, category: str, issue_type: str) -> str:
    """归因指纹 = 报告来源 + 缺陷类别 + 报告问题类型。

    刻意不含"本次上传的文件哈希"：同一份报告重传一次不该再落一批归因，
    否则库里会出现内容相同、只有指纹不同的重复归因，派生时的"重复派生"拦截
    也就形同虚设。重传的幂等由这个指纹保证。
    """
    raw = f"case-review:{review_id}:{category}:{issue_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _signal_for_category(category: str) -> str:
    """把归因类别翻成人工信号词。

    未知类别这里会抛异常而不是给个兜底值：能走到归因落库的类别必然由
    ``CaseReviewReportParser`` 产出，只有上面那两类。真冒出第三类说明
    解析层加了新类别却忘了定义它的信号，静默兜底会让护栏带上错误的信号词，
    比直接报错难查得多。
    """
    try:
        return SIGNAL_BY_CATEGORY[category]
    except KeyError as exc:
        raise ValueError(f"未知的归因类别，无法确定人工信号：{category}") from exc


class CaseReviewEvolutionService:
    """用例审查的单次自进化入口（T23）。"""

    # ------------------------------------------------------------ 列出可进化的审查项目

    @classmethod
    def list_candidates(cls, *, project, limit: int = 50) -> list:
        """列出该项目下**已跑完且可用于自进化**的用例审查项目。

        每条的 ``evolvable`` 与 ``blockers`` 都按后面 ``evolve`` 的真实判据算，
        不在前端另写一套"看起来能不能" —— 否则会出现"页面说可以、接口报错"。
        """
        from testcases.models import TestCaseReview

        reviews = (
            TestCaseReview.objects.filter(project=project, status="completed")
            .select_related("creator")
            .order_by("-completed_at", "-created_at")[:limit]
        )
        return [cls._describe(review) for review in reviews]

    @classmethod
    def _describe(cls, review) -> dict:
        summary = review.summary or {}
        output = cls._output_of(review)
        skill_version = cls._skill_version_of(review, output)

        blockers: list = []
        if output is None:
            blockers.append("该审查尚未把产出发布到数据飞轮，没有可归因的产出记录")
        if output is not None and skill_version is None:
            blockers.append("本次审查未锁定 Skill 版本，改进无处落地")
        if skill_version is not None:
            skill = skill_version.skill
            if skill.active_version_id and str(skill.active_version_id) != str(skill_version.id):
                blockers.append(
                    f"审查时用的 {skill_version.version} 已不是当前活跃版本，"
                    f"派生基线必须是活跃版本（当前 {skill.active_version.version}）"
                )

        derived = []
        if skill_version is not None:
            # 版本本身没有 state —— 状态挂在它的发布单元上（``release.state``）。
            # 直接取 ``SkillVersion.state`` 会 FieldError，而这是个"读到就崩"的错误，
            # 不是静默降级，反而好排查。
            derived = list(
                skill_version.skill.versions.filter(source_type="evolution")
                .order_by("-created_at")
                .values("id", "version", "release__state", "created_at")[:5]
            )

        return {
            "review_id": str(review.pk),
            "source_name": review.source_name,
            "review_mode": review.review_mode,
            "skill_name": review.skill_name,
            "status": review.status,
            "completed_at": review.completed_at or review.updated_at,
            "created_at": review.created_at,
            "creator": getattr(review.creator, "username", "") if review.creator else "",
            "output_id": str(output.id) if output else "",
            "trace_id": summary.get("trace_id", ""),
            "issues_count": summary.get("issues_count"),
            "skill_version": skill_version.version if skill_version else "",
            "skill_version_id": str(skill_version.id) if skill_version else "",
            "package_sha256": skill_version.package_sha256 if skill_version else "",
            "skill_id": str(skill_version.skill_id) if skill_version else "",
            "is_active_version": bool(
                skill_version and str(skill_version.skill.active_version_id) == str(skill_version.id)
            ),
            "evolvable": not blockers,
            "blockers": blockers,
            "derived_candidates": [
                {
                    "version_id": str(item["id"]),
                    "version": item["version"],
                    "state": item["release__state"] or "",
                    "created_at": item["created_at"],
                }
                for item in derived
            ],
        }

    @staticmethod
    def _output_of(review):
        """取这次审查在飞轮里的产出。"""
        output_id = (review.summary or {}).get("output_id")
        if not output_id:
            return None
        return (
            GenerationOutput.objects.filter(pk=output_id)
            .select_related("skill_version", "skill_version__skill", "trace", "capability")
            .first()
        )

    @staticmethod
    def _skill_version_of(review, output, *, persist: bool = False):
        """返回审查实际选用的 Skill 版本，并兼容早期断链产出。

        新任务应从 ``GenerationOutput.skill_version`` 读取精确版本；但已有审查可能
        已保存 ``selected_skill``，产出却因旧链路漏写版本而为空。此时按审查时明确
        选择的 Skill 解析当前可运行版本，让人工报告仍能进入自进化流程。真正发起
        派生时顺手补齐产出绑定，后续反馈、归因和历史查询都回到同一条版本链上。
        """
        if output is None:
            return None
        if output.skill_version_id:
            return output.skill_version

        selected_skill = getattr(review, "selected_skill", None)
        if selected_skill is None:
            return None

        from skills.runtime import SkillRuntimeResolver

        skill_version = SkillRuntimeResolver.runnable_version(selected_skill)
        if skill_version is not None and persist:
            output.skill_version = skill_version
            output.skill_package_sha256 = skill_version.package_sha256
            output.save(update_fields=["skill_version", "skill_package_sha256"])
        return skill_version

    # ------------------------------------------------------------ 发起自进化

    @classmethod
    def scan(cls, *, data: bytes) -> ReportScan:
        """只解析、不落库。供向导在上传那一刻给出预检结果。"""
        return CaseReviewReportParser.parse(data)

    @classmethod
    def evolve(
        cls, *, review, data: bytes, actor,
        threshold: float | None = None, change_reason: str = "",
        report_name: str = "", attribution_ids=None,
    ) -> dict:
        """从一份已确认报告派生 Skill 候选版本。

        改进依据有两个来源，由 ``attribution_ids`` 决定走哪条：

        - 传了 id 列表 → 用**人工已确认的候选优化点**（AI 提、人确认的主路径）；
        - 没传 → 降级用**人工在报告里写下的结论**（原路径，未配置 LLM 时走这条）。

        ``threshold`` 现已降级为采纳率的**参考线**：它只影响返回值里的
        ``below_reference`` 标记，**不影响是否允许派生**。
        采纳率是版本间对比的评分维度，不是准入门槛 —— 低采纳率意味着
        "这一版效果还不理想"，而效果不理想恰是进化的理由，不是拒绝进化的理由。

        顺序刻意是"先解析、再校验、再派生"：解析失败或没有可用归因时，
        库里不该留下痕迹，否则用户会看到一批"凭空出现的已确认归因"，
        却不知道是哪次失败的尝试带进来的。
        """
        from django.db import transaction

        from .skill_evolution import SkillEvolutionService

        reference = (
            DEFAULT_HUMAN_SCORE_THRESHOLD if threshold is None else float(threshold)
        )
        if review.status != "completed":
            raise ValidationError(f"该审查当前状态是「{review.get_status_display()}」，只有已完成的审查才能自进化")

        output = cls._output_of(review)
        if output is None:
            raise ValidationError("该审查没有飞轮产出记录，无法归因（产出缺失时无从判断是谁的错）")
        skill_version = cls._skill_version_of(review, output, persist=True)
        if skill_version is None:
            raise ValidationError("该审查没有可用的已选 Skill 版本，改进无处落地；请先确认审查所选 Skill 仍可运行")

        skill = skill_version.skill
        if skill.active_version_id and str(skill.active_version_id) != str(skill_version.id):
            raise ValidationError(
                f"审查使用的 {skill_version.version} 已不是当前活跃版本，"
                f"派生基线必须是活跃版本（当前 {skill.active_version.version}）"
            )

        scan = CaseReviewReportParser.parse(data)
        score = scan.acceptance_score
        # 采纳率不拦截派生，理由见方法 docstring。

        if not attribution_ids and not scan.defects:
            raise ValidationError(
                f"这份报告里没有可修复的缺陷：人工确认采纳 {scan.affirmative} 条、"
                f"标记误报 {scan.negative} 条、未确认 {scan.unconfirmed} 条。"
                "自进化需要至少一条可归因到本 Skill 的缺陷。"
            )

        with transaction.atomic():
            if attribution_ids:
                attributions = cls._confirmed_attributions(output=output, ids=attribution_ids)
                if not attributions:
                    raise ValidationError(
                        "所选优化点没有一条处于「已确认」状态，无法作为派生依据；"
                        "请先在候选优化点列表里逐条确认。"
                    )
            else:
                # 降级路径：未走 AI 候选时，人工在报告里写下的结论直接作为依据。
                attributions = cls._build_attributions(review, output, scan, actor)
            result = SkillEvolutionService.derive_candidate(
                skill_version=skill_version,
                attributions=attributions,
                actor=actor,
                change_reason=change_reason or (
                    f"用例审查 {review.source_name} 人工确认报告派生："
                    f"误报 {scan.negative} 条、说明被改写 {scan.rewritten} 条"
                ),
                expected_benefit=(
                    "减少 " + "、".join(sorted({item.issue_type for item in scan.defects}))
                    + " 类问题的误报与不可执行表述"
                ),
                impact_scope=f"仅生成 {skill.name} 的新候选版本，不影响当前活跃版本",
            )
            feedback = cls._record_human_score(
                review=review, output=output, skill_version=skill_version,
                score=score, threshold=reference, actor=actor, scan=scan,
                report_name=report_name,
                report_sha256=hashlib.sha256(data).hexdigest(),
            )

        candidate = result["candidate"]
        return {
            "review_id": str(review.pk),
            "source_name": review.source_name,
            "skill_id": str(skill.id),
            "skill_name": skill.name,
            "baseline_version": skill_version.version,
            "baseline_package_sha256": skill_version.package_sha256,
            "human_score": score,
            "acceptance_score": score,
            "threshold": reference,
            "acceptance_reference": reference,
            # 采纳率作为**版本间对比的评分维度**：低于参考线只是标记，
            # 前端据此标黄提示，不构成拦截。
            "below_reference": score < reference,
            "version_acceptance": cls.acceptance_history(skill),
            "attribution_source": "llm+human" if attribution_ids else "human",
            "feedback_id": str(feedback.id) if feedback else "",
            "scan": scan.as_dict(),
            "attribution_ids": [str(item.id) for item in attributions],
            "candidate": {
                "version_id": str(candidate.id),
                "version": candidate.version,
                "state": candidate.state,
                "package_sha256": candidate.package_sha256,
                "change_reason": getattr(candidate, "change_reason", ""),
                "created_at": candidate.created_at,
            },
            "diff": result.get("diff") or {},
            "rollback_target": result.get("rollback_target", ""),
            "active_untouched": result.get("active_untouched", False),
            # 尾斜杠不能省：DRF 的路由带尾斜杠，少一个会 301。
            # 前端拿这个 URL 下 blob，多一次重定向不但慢，某些运行环境下
            # 自定义 Authorization 头在重定向后还会丢，表现为"下载了一个 401 页面"。
            "download_url": (
                f"/api/projects/{skill.project_id}/skills/{skill.id}"
                f"/versions/{candidate.id}/download/"
            ),
        }

    @classmethod
    def record_report_feedback(
        cls, *, review, data: bytes, actor, report_name: str = "",
        threshold: float | None = None,
    ) -> dict:
        """上传已确认报告，只记录采纳率反馈，不派生 Skill 版本。"""
        if review.status != "completed":
            raise ValidationError("只有已完成的审查才能上传质量反馈")
        output = cls._output_of(review)
        if output is None:
            raise ValidationError("该审查没有飞轮产出记录，无法关联质量反馈")
        skill_version = cls._skill_version_of(review, output, persist=True)
        if skill_version is None:
            raise ValidationError("该审查没有可用的已选 Skill 版本，无法关联质量反馈")
        threshold = DEFAULT_HUMAN_SCORE_THRESHOLD if threshold is None else float(threshold)
        scan = CaseReviewReportParser.parse(data)
        feedback = cls._record_human_score(
            review=review,
            output=output,
            skill_version=skill_version,
            score=scan.acceptance_score,
            threshold=threshold,
            actor=actor,
            scan=scan,
            report_name=report_name,
            report_sha256=hashlib.sha256(data).hexdigest(),
        )
        return {
            "review_id": str(review.pk),
            "feedback_id": str(feedback.id),
            "human_score": scan.acceptance_score,
            "acceptance_score": scan.acceptance_score,
            "threshold": threshold,
            "acceptance_reference": threshold,
            "scan": scan.as_dict(),
            "version_acceptance": cls.acceptance_history(skill_version.skill),
        }

    @classmethod
    def propose_optimizations(cls, *, review, data: bytes, actor) -> dict:
        """四要素 → AI 候选优化点。**只提候选，不派生。**

        与派生分开是刻意的：提候选是"读一份报告、给一堆看法"，派生是"改包"。
        合并成一个动作，用户就没法"先看看 AI 想改什么、再决定改不改"，
        而这正是这次要做的改进。
        """
        from .report_optimization import ReportOptimizationAdvisor

        if review.status != "completed":
            raise ValidationError(
                f"该审查当前状态是「{review.get_status_display()}」，只有已完成的审查才能提优化建议"
            )
        output = cls._output_of(review)
        if output is None:
            raise ValidationError("该审查没有飞轮产出记录，无法关联到具体产出做归因")
        if not data:
            raise ValidationError("必须上传已确认的审查报告")

        # 早期用例审查已把实际选用的 Skill 记在 review.selected_skill，
        # 但 GenerationOutput.skill_version 可能因旧链路漏写而为空。预检和派生
        # 已经能按审查任务回填精确版本；候选生成必须共用同一套规则，
        # 否则会出现“项目可进化，但读不到 Skill 包”的自相矛盾。
        skill_version = cls._skill_version_of(review, output, persist=True)
        if skill_version is None:
            raise ValidationError("该审查没有可用的已选 Skill 版本，无法读取包正文生成候选")

        scan = CaseReviewReportParser.parse(data)
        result = ReportOptimizationAdvisor().propose(
            output=output, scan=scan, skill_version=skill_version, actor=actor,
        )
        return {
            "review_id": str(review.pk),
            "scan": scan.as_dict(),
            "output_id": str(output.pk),
            **result,
        }

    @classmethod
    def decide_optimization(
        cls, *, review, attribution_id, action: str, actor,
        hypothesis: str = "", category: str = "", primary_cause: str = "",
        fine_cause: str = "", repair_target: str = "",
    ) -> dict:
        """人工对一条候选优化点做结论（采纳 / 改写 / 驳回）。"""
        from .report_optimization import ReportOptimizationAdvisor

        output = cls._output_of(review)
        if output is None:
            raise ValidationError("该审查没有飞轮产出记录，无法定位候选优化点")
        attribution = FailureAttribution.objects.filter(
            id=str(attribution_id), project_id=review.project_id, output_id=output.pk,
        ).first()
        if attribution is None:
            raise ValidationError("找不到该候选优化点（可能已被重新生成替换）")
        return ReportOptimizationAdvisor.decide(
            attribution=attribution, actor=actor, action=action,
            hypothesis=hypothesis, category=category,
            primary_cause=primary_cause, fine_cause=fine_cause,
            repair_target=repair_target,
        )

    @classmethod
    def acceptance_history(cls, skill, *, limit: int = 12) -> list[dict]:
        """按版本横向列出采纳率 —— 「采纳率作为版本间对比评分维度」的落地。

        实现在 ``WorkflowStageFeedbackService.acceptance_history``：四阶段与用例审查
        读的是同一张 ``FeedbackEvent``、同一个 ``reason_code`` 族，**查询只能有一份**。
        两处各写一遍，一旦口径调整（比如新增一个历史 reason_code），
        就会有一边漏捞，表现为"某个版本在对比图里凭空消失"。
        """
        from .workflow_feedback import WorkflowStageFeedbackService

        return WorkflowStageFeedbackService.acceptance_history(skill, limit=limit)

    @staticmethod
    def _confirmed_attributions(*, output, ids) -> list:
        """取人工已确认的候选归因，且必须落在同一项目内。

        归属校验不是为了权限（这里已经是项目成员的路径），而是防止把 A 项目的
        结论拿去改 B 项目所依赖的包：归因 id 虽然是 UUID，但它会出现在页面 URL
        与导出的报告里，跨项目复用是可能发生的误操作。
        """
        normalized = [str(item) for item in (ids or []) if str(item).strip()]
        if not normalized:
            return []
        rows = list(
            FailureAttribution.objects.filter(
                id__in=normalized,
                project_id=output.project_id,
                state="confirmed",
            ).order_by("created_at")
        )
        return rows

    @classmethod
    def _build_attributions(cls, review, output, scan: ReportScan, actor) -> list:
        """把解析出的缺陷落成 ``confirmed`` 归因（同指纹复用，不重复落库）。

        ⚠️ 这是**降级路径**：直接采信人工在报告里写下的结论。
        主路径是 AI 提候选、人工确认（``report_optimization``）。
        两条路径都落 ``confirmed``，但 ``source`` 不同：
        这里落 ``human``（结论确实由人给出），主路径落 ``llm`` + 确认人留痕。
        区分开才能事后回答"这条护栏究竟是模型猜的还是人写的"。
        """
        attributions = []
        for defect in scan.defects:
            fingerprint = _attribution_fingerprint(review.pk, defect.category, defect.issue_type)
            evidence = [
                {
                    "source_type": "case_review_confirmed_report",
                    "source_id": str(review.pk),
                    "feedback_signals": [_signal_for_category(defect.category)],
                    "issue_type": defect.issue_type,
                    **sample,
                }
                for sample in defect.samples
            ]
            attribution, created = FailureAttribution.objects.get_or_create(
                fingerprint=fingerprint,
                defaults={
                    "project_id": review.project_id,
                    "output": output,
                    "workflow_id": str(review.pk),
                    "category": defect.category,
                    "source": "human",
                    "confidence": 1.0,
                    "hypothesis": defect.hypothesis,
                    "evidence": evidence,
                    "state": "confirmed",
                    "confirmed_by": actor,
                    "confirmed_at": timezone.now(),
                },
            )
            if not created and attribution.state != "confirmed":
                # 人工这次把它确认了，就补上确认留痕；不改已经确认过的内容。
                attribution.state = "confirmed"
                attribution.confirmed_by = actor
                attribution.confirmed_at = timezone.now()
                attribution.save(update_fields=["state", "confirmed_by", "confirmed_at", "updated_at"])
            attributions.append(attribution)
        return attributions

    @classmethod
    def _record_human_score(
        cls, *, review, output, skill_version, score, threshold, actor, scan, report_name,
        report_sha256="",
    ):
        """人工打分落 ``FeedbackEvent``。

        走这张表而不是新造一张：``FeedbackEvent`` 已经有 ``value``（分值）、
        ``skill_version``（绑定到生成它的版本）与 ``idempotency_key``（幂等）三样
        本用例正好需要的东西，新建一张表只会让"反馈"散在两处。

        ``signal`` 取 ``accepted``：语义是"人工认可本次审查结果可作为依据"，
        与"分数达到门槛才允许进化"是同一条判断，不另立枚举。
        """
        report_digest = (report_sha256 or hashlib.sha256(
            f"{review.pk}:{score:g}:{report_name}".encode("utf-8")
        ).hexdigest())[:16]
        event, _created = FeedbackEvent.objects.get_or_create(
            idempotency_key=f"case-review-evolution:{review.pk}:{report_digest}",
            defaults={
                "project_id": review.project_id,
                "output": output,
                "trace": output.trace,
                "capability": output.capability,
                "capability_kind": "skill",
                "skill_version": skill_version,
                "signal": "accepted",
                "value": round(score / 100, 4),
                "reason_code": "report_acceptance_rate",
                "comment": (
                    f"用例审查报告最后一个 Sheet 的采纳率为 {score:g}%"
                    f"（门槛 {threshold:g}%）；"
                    f"确认采纳 {scan.affirmative} 条、误报 {scan.negative} 条、"
                    f"说明被改写 {scan.rewritten} 条、未确认 {scan.unconfirmed} 条"
                ),
                "detail": {
                    "kind": "case_review_evolution",
                    "review_id": str(review.pk),
                    "report_name": report_name,
                    "threshold": threshold,
                    "scan": scan.as_dict(),
                },
                "actor": actor,
                "actor_type": "user",
            },
        )
        return event
