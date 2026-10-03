"""报告解析公共件：把「采纳率」这一条跨能力通用的约定从用例审查里提出来。

背景
----
用例审查（T23）已验证「上传已确认报告 → 读采纳率 → 作为该次产出的质量分数」这条路径。
全链路测试的四阶段（方案 / 用例 / 执行 / 报告）要复用同一条路径，但**四份报告的结构
互不相同**，整份解析器无法共用。

能共用的只有一条约定：

    报告最后一个 Sheet 内，用「采纳率」标签单元格记录本轮的人机一致率。

平台只按标签读，**不绑坐标** —— 绑坐标会在报告模板换版时静默读错格，
产出"看起来很正常的错误分数"。四阶段的报告生成 Skill 必须遵守这条约定，
否则该次产出记为"未评分"（不是失败）。

采纳率的角色（2026-10-03 用户口径）
----------------------------------
**它只作为不同版本间对比的评分维度，不作为能否上传或能否进化的门槛。**

所以本模块**不提供**"低于多少就拒绝"的判断。拒绝若存在，应由治理侧显式配置
（见 ``capability_registry`` / 发布门禁），而不是写死在解析层 ——
写死在解析层会让"记录一个分数"和"用一个分数做决策"这两件事再也分不开。

「skill 都是一点点优化出来的」是这条口径的由来：把门槛做成硬阻断，
等于要求每一个中间版本都必须一次跨过同一条线，而进化本来就是逐步逼近的。
"""
from __future__ import annotations

from django.core.exceptions import ValidationError

#: 报告末页用于定位质量分数的标签。改名等于换协议，四阶段与本模块必须同时改。
ACCEPTANCE_LABEL = "采纳率"

#: 采纳率的**参考线**（百分制）。语义是"低于这条线值得看一眼"，
#: **不是**"低于这条线就不许进化"。展示层据此标黄，流程不据此拒绝。
DEFAULT_ACCEPTANCE_REFERENCE = 70.0


def cell_text(value) -> str:
    """单元格取文本。数字型「行号」要按整数渲染，否则 12 会变成 "12.0"。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_text(value) -> str:
    """归一化人工填写值：去空白、去全角空格、转小写。"""
    return cell_text(value).replace("\u3000", "").replace(" ", "").lower()


def _coerce_rate(raw) -> float | None:
    """把单元格里的值收敛成 0–1 的比率。

    ``None`` / 空文本 / 公式无缓存值 → 返回 ``None``（调用方决定是否重算）。
    有值但越界或非数字 → 抛 ``ValidationError``：那是模板错了，
    静默当 0 会让"报告模板有问题"表现成"这一版质量很差"，两种结论天差地别。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValidationError(f"「{ACCEPTANCE_LABEL}」不是有效数字")
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1:
            value /= 100
        if 0 <= value <= 1:
            return value
        raise ValidationError(f"「{ACCEPTANCE_LABEL}」必须在 0%–100% 之间")
    text = cell_text(raw)
    if not text:
        return None
    if text.startswith("="):
        # 公式且没有缓存值。平台导出的报告采纳率正是公式，
        # 所以这条路径是常态而不是异常 —— 交给调用方按同口径重算。
        return None
    percent = text.endswith("%")
    try:
        value = float(text.rstrip("%"))
    except ValueError as exc:
        raise ValidationError(f"「{ACCEPTANCE_LABEL}」不是有效数字") from exc
    return _coerce_rate(value / 100 if percent else value)


def locate_acceptance(sheet) -> tuple[bool, float | None]:
    """在 ``sheet`` 里按标签定位「采纳率」。

    返回 ``(是否找到标签, 比率)``：

    - ``(False, None)`` —— 这份报告不提供该维度（不同模板/不同阶段）。
    - ``(True, None)``  —— 找到标签但值是公式且无缓存值，需按同口径重算。
    - ``(True, 0.85)``  —— 正常读到。
    """
    for row in sheet.iter_rows():
        for index, cell in enumerate(row):
            if normalize_text(cell.value) != ACCEPTANCE_LABEL:
                continue
            if index + 1 >= len(row):
                raise ValidationError(f"「{ACCEPTANCE_LABEL}」紧邻的单元格为空，无法取值")
            return True, _coerce_rate(row[index + 1].value)
    return False, None


def read_acceptance_rate(sheet) -> float | None:
    """读取 ``sheet`` 里的采纳率；没有该标签时返回 ``None``（不报错）。

    与 :func:`read_acceptance_from_workbook` 的分工：本函数回答"读到没有"，
    后者回答"这份报告够不够格当依据"。
    """
    _found, value = locate_acceptance(sheet)
    return value


def read_acceptance_from_workbook(
    workbook, *, required: bool = False,
) -> tuple[float | None, str]:
    """取报告**最后一个 Sheet** 的采纳率，返回 ``(比率, 页名)``。

    ``required=True`` 时缺标签直接抛错 —— 用例审查走这条：它的报告由平台自己导出，
    没有采纳率页说明上传的不是平台报告，继续下去只会拿一份错文件当依据。
    四阶段走 ``required=False``：报告由各阶段 Skill 生成，没有采纳率只是"未评分"。
    """
    sheet = workbook.worksheets[-1]
    found, value = locate_acceptance(sheet)
    if not found and required:
        raise ValidationError(
            f"报告最后一个 Sheet 缺少「{ACCEPTANCE_LABEL}」，"
            "请上传平台导出并完成人工确认的报告"
        )
    return value, sheet.title
