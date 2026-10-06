"""阶段报告反馈：把「已完成阶段的人机一致率」记成质量反馈，**不派生**。

为什么与派生分开
----------------
这是 2026-10-03 明确的一条职责边界：

- **反馈**回答"这一版产出被人工认可了多少" —— 是**记录**。
- **派生**回答"下一版要改什么" —— 是**改包**。

做成一个动作，用户想"只记录一次评审结论"时会被迫改包；而改包是不可逆的
（写新的不可变版本目录、动 Skill 包），不该被一次记录动作顺带触发。
所以本模块只有一个出口：解析采纳率 → 落 ``FeedbackEvent`` → 返回。

口径（两条要分开看，很容易混）
------------------------------
1. **采纳率的数值不是门槛**。低于参考线照常入库，只在返回值里标
   ``below_reference`` 供页面标黄。理由：skill 是一点点优化出来的，
   把参考线做成硬阻断等于要求每个中间版本一次跨过同一条线。
2. **报告的格式是契约**。报告末页读不到「采纳率」就拒绝，并说清期望格式。
   前者的失败是"质量不够"，后者的失败是"文件本身不对"；
   把两件事揉成一句"不达标"，用户会去查质量而不是查文件。

阶段报告由各阶段 Skill 生成，所以第 2 条不是平台能单方面保证的事 ——
它靠这条明确报错来暴露"这个阶段的 Skill 还没按约定输出采纳率"。
"""
from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from django.core.exceptions import ValidationError

from .models import FeedbackEvent
from .report_parsing import (
    ACCEPTANCE_LABEL,
    DEFAULT_ACCEPTANCE_REFERENCE,
    read_acceptance_from_workbook,
)

logger = logging.getLogger(__name__)

#: 平台按这个语义给反馈打标：``reason_code`` 决定它属于"报告采纳率"这一族，
#: 与用例审查路径共用同一个取值，``acceptance_history`` 才捞得全。
REASON_CODE = "report_acceptance_rate"


class WorkflowStageFeedbackService:
    """阶段反馈的唯一入口：读采纳率、落反馈。"""

    #: 采纳率族反馈的 ``reason_code`` 白名单。``manual_calibration`` 是口径变化前
    #: 人工校准留下的历史取值，读历史时必须一起捞，否则"版本对比"会在某个版本处断档。
    ACCEPTANCE_REASON_CODES = ("report_acceptance_rate", "manual_calibration")

    @staticmethod
    def acceptance_history(skill, *, limit: int = 12) -> list[dict]:
        """按版本横向列出采纳率 —— 「采纳率作为版本间对比评分维度」的唯一实现。

        刻意从 ``FeedbackEvent`` 读而不是从 ``GenerationOutput``：采纳率是
        "人对某一版产出质量的评价"，而 ``FeedbackEvent`` 正是带着 ``skill_version``
        绑定落库的那张表。产出表里没有这个维度，硬塞进去会让"产出"承担两件事。

        返回按时间倒序；页面据此画"版本 → 采纳率"的对照，让"skill 是一点点
        优化出来的"这件事可见 —— 而不是只有一个孤零零的当前值。
        """
        if skill is None:
            return []
        rows = (
            FeedbackEvent.objects
            .filter(
                skill_version__skill=skill,
                reason_code__in=WorkflowStageFeedbackService.ACCEPTANCE_REASON_CODES,
            )
            .select_related("skill_version")
            .order_by("-occurred_at")[:limit]
        )
        return [
            {
                "version": row.skill_version.version if row.skill_version_id else "",
                "version_id": str(row.skill_version_id) if row.skill_version_id else "",
                "score": round((row.value or 0) * 100, 2),
                "at": row.occurred_at.isoformat() if row.occurred_at else "",
                "reason_code": row.reason_code,
            }
            for row in rows
        ]

    @staticmethod
    def read_acceptance(data: bytes) -> tuple[float | None, str]:
        """从上传报告里读采纳率，返回 ``(比率, 页名)``。

        只认 Excel（``.xlsx``/``.xlsm``）：阶段报告与平台导出报告同一形态，
        末页带「采纳率」标签单元格。上传别的格式不是"读不到"，而是**明确不是**
        这份约定的东西，所以直接拒绝并说清期望格式 —— 拿一份 Word 报告静默
        落一条"未评分"反馈，用户会以为记录成功了。

        返回的比率是 0–1 的小数（与 ``FeedbackEvent.value`` 同口径）。
        缺标签返回 ``None``，由调用方决定是否拒绝；**本函数不拒绝**，
        这样同一个解析入口既能服务"必须有分"的端点，也能服务将来
        "只想记一笔评审"的场景。
        """
        if not data:
            raise ValidationError("报告文件为空，无法解析")
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(BytesIO(data), data_only=True, read_only=True)
        except ValidationError:
            raise
        except Exception as exc:  # pragma: no cover - openpyxl 异常类型不稳定
            raise ValidationError(f"报告无法作为 Excel 打开：{exc}") from exc

        value, sheet_name = read_acceptance_from_workbook(workbook, required=False)
        return value, sheet_name

    @classmethod
    def record(
        cls, *, output, stage: str, data: bytes, actor, report_name: str = "",
        reference: float | None = None,
    ) -> dict:
        """记一条阶段反馈。**不派生、不动 Skill 版本、不改产出。**

        Args:
            output: 该阶段的 ``GenerationOutput``（调用方已按三元定位好，
                本模块不再重复定位 —— 定位口径只有一处，见
                ``WorkflowGateService.locate_stage_output``）。
            stage: 阶段 key，用于文案与幂等键。
            data: 报告文件字节。
            actor: 上传人。
            report_name: 原始文件名，仅用于留痕与展示。
            reference: 参考线（百分制）。只影响 ``below_reference`` 这个**展示位**，
                不参与是否入库的判定。默认取公共件里的 70。
        """
        if output is None:
            raise ValidationError("该阶段暂无产出，无法关联质量反馈")

        score, sheet_name = cls.read_acceptance(data)
        if score is None:
            raise ValidationError(
                f"报告最后一个 Sheet「{sheet_name}」里没有「{ACCEPTANCE_LABEL}」单元格，"
                f"无法作为质量依据；请上传平台导出并完成人工确认的报告，"
                f"该阶段的 Skill 需在报告末页输出「{ACCEPTANCE_LABEL}」"
            )

        reference = (
            DEFAULT_ACCEPTANCE_REFERENCE if reference is None else float(reference)
        )
        percent = round(score * 100, 2)
        report_sha256 = hashlib.sha256(data).hexdigest()
        event, created = cls._record(
            output=output, stage=stage, score=score, actor=actor,
            report_name=report_name, reference=reference, report_sha256=report_sha256,
            sheet_name=sheet_name,
        )
        return {
            "stage": stage,
            "output_id": str(output.pk),
            "feedback_id": str(event.id),
            "acceptance_score": percent,
            # 展示位：低于参考线标黄提示，**不构成拦截**。
            "below_reference": percent < reference,
            "acceptance_reference": reference,
            "report_name": report_name,
            "acceptance_sheet": sheet_name,
            "skill_version": output.skill_version.version if output.skill_version_id else "",
            # 重复上传同一份报告会命中幂等键：反馈已存在，只是又提交了一次。
            # 页面据此提示"已记录过"，而不是让人以为新建了一条。
            "created": created,
        }

    @classmethod
    def _record(
        cls, *, output, stage, score, actor, report_name, reference, report_sha256,
        sheet_name,
    ) -> tuple[FeedbackEvent, bool]:
        """落 ``FeedbackEvent``；同一份报告重复上传不产生第二条。

        幂等键含报告 sha256：**同一阶段、同一份文件**重复上传是同一个事件；
        换了文件（哪怕只改一个格）就是一个新事件 —— 那代表"重新评审了一次"，
        不该被去重吃掉。用 ``get_or_create`` 而不是先查后建，
        是为了让并发上传也只有一条命中唯一约束。

        返回 ``(事件, 是否新建)``。
        """
        percent = round(score * 100, 2)
        digest = report_sha256[:16]
        event, created = FeedbackEvent.objects.get_or_create(
            idempotency_key=f"workflow-stage-feedback:{output.project_id}:{stage}:{digest}",
            defaults={
                "project_id": output.project_id,
                "output": output,
                "trace": output.trace,
                "capability": output.capability,
                "capability_kind": "skill",
                "skill_version": output.skill_version,
                "signal": "accepted",
                "value": round(score, 4),
                "reason_code": REASON_CODE,
                "comment": (
                    f"{stage} 阶段报告「{sheet_name}」页采纳率为 {percent:g}%"
                    f"（参考线 {reference:g}%）"
                ),
                "detail": {
                    "kind": "workflow_stage_feedback",
                    "stage": stage,
                    "report_name": report_name,
                    "report_sha256": report_sha256,
                    "reference": reference,
                    "below_reference": percent < reference,
                    "acceptance_sheet": sheet_name,
                    "skill_version": (
                        output.skill_version.version if output.skill_version_id else ""
                    ),
                },
                "actor": actor,
                "actor_type": "user",
            },
        )
        return event, created


# ---------------------------------------------------------------------------
# 确认报告闭环（T09 / §7）
# ---------------------------------------------------------------------------

#: 确认报告反馈的 ``reason_code``。
#:
#: 与 ``REASON_CODE``（只记一个采纳率）**刻意分开**：这条流带的是一整套逐项
#: 人机对比结论（采纳/修改后采纳/删除 + 修改类型）。混成一个 reason_code 会让
#: "采纳率版本对比"图里混进两份口径不同的数据，而它们的数值不可比。
REVIEW_REASON_CODE = "stage_review_report"

#: 反馈状态真值。
REVIEW_STATE_DRAFT = "draft"
REVIEW_STATE_SUBMITTED = "submitted"
REVIEW_STATES: tuple[str, ...] = (REVIEW_STATE_DRAFT, REVIEW_STATE_SUBMITTED)

#: 正式提交时的信号：有空白也允许提交（人可能故意只审一部分），
#: 但**草稿永远不能进进化**——用信号区分，让下游按信号过滤而不是靠猜。
REVIEW_SIGNAL = {REVIEW_STATE_DRAFT: "edited", REVIEW_STATE_SUBMITTED: "accepted"}


class ReviewSubmissionError(Exception):
    """正式提交校验失败，**带逐行错误**。

    为什么不用 Django ``ValidationError`` 传结构化错误：它的 ``messages`` 会把
    dict 展平成字符串列表，逐行信息（"第 3 行缺修改类型"）在半路就丢掉了，
    页面只能显示一句"提交校验未通过"，用户得回去一行行找。这里显式带
    ``errors``，由视图层原样交给前端。
    """

    def __init__(self, errors, detail: str = "") -> None:
        self.errors = list(errors or [])
        self.detail = detail or (
            "存在必填项缺失或非法取值，无法正式提交；可先保存草稿继续填写"
        )
        super().__init__(self.detail)


class WorkflowStageReviewService:
    """确认报告的草稿与正式提交（T09）。

    两条路径**共用同一个幂等键**：同一份文件先存草稿、再正式提交，是同一条记录的
    状态推进，而不是两条反馈。重复上传同一份已提交的文件也命中同一条——"再点一次
    提交"不该在版本对比图里多出一个点。

    能不能进进化由 ``detail['state']`` 决定，不由"能不能上传"决定：
    草稿允许有空白（人可能只审了一部分），正式提交才校验条件必填。
    """

    @classmethod
    def save_draft(cls, *, output, stage: str, data: bytes, actor, report_name: str = "") -> dict:
        """保存草稿：**不做条件必填校验**，只要求文件结构可解析。"""
        return cls._apply(
            output=output, stage=stage, data=data, actor=actor,
            report_name=report_name, state=REVIEW_STATE_DRAFT,
        )

    @classmethod
    def submit(cls, *, output, stage: str, data: bytes, actor, report_name: str = "") -> dict:
        """正式提交：逐行校验条件必填关系，不通过直接拒绝并回具体行。"""
        return cls._apply(
            output=output, stage=stage, data=data, actor=actor,
            report_name=report_name, state=REVIEW_STATE_SUBMITTED,
        )

    @classmethod
    def _apply(
        cls, *, output, stage: str, data: bytes, actor, report_name: str, state: str,
    ) -> dict:
        from .review_reports import ReviewReportFormatError, parse_review_workbook
        from .operations import WorkflowGateService

        if output is None:
            raise ValidationError("该阶段暂无产出，无法关联人工确认结论")

        try:
            parsed = parse_review_workbook(data)
        except ReviewReportFormatError as exc:
            # 文件本身不对 → 明确报格式，而不是报"未评分"。
            raise ValidationError(str(exc)) from exc

        validation = parsed["validation"]
        if state == REVIEW_STATE_SUBMITTED and not validation["ok"]:
            raise ReviewSubmissionError(validation["errors"])

        statistics = parsed["statistics"]
        fingerprint = parsed["fingerprint"]
        report_sha256 = hashlib.sha256(data).hexdigest()
        key = (
            f"workflow-stage-review:{output.project_id}:{stage}:{report_sha256[:16]}"
        )

        detail = {
            "kind": "workflow_stage_review",
            "stage": stage,
            "state": state,
            "report_name": report_name,
            "report_sha256": report_sha256,
            "rows_fingerprint": fingerprint,
            "statistics": statistics,
            "blank_count": validation["blank_count"],
            # 已审核行数与原样采纳率是下游（T11 差异、T13 候选）唯一需要的两个索引值；
            # 其余指标留在 statistics 里，供页面展示。
            "reviewed_count": statistics["已审核数"],
            # 低采纳率**不是门槛**：照常入库，只是把"低于参考线"标出来。
            "evolvable": state == REVIEW_STATE_SUBMITTED,
            # 人工填过的行**原文留档**（T11）：差异与归因要在提交之后还能算，
            # 而报告文件由人保管、可能已离线流转几天。只存"填过的行"而不是整份
            # 报告——没填的行不参与差异（未审 ≠ 采纳），存下来只会撑大这张表。
            "human_rows": parsed["human_rows"],
        }

        percent = round(float(statistics.get("采纳率") or 0) * 100, 2)
        event, created = ReviewFeedbackEventStore.upsert(
            output=output, stage=stage, key=key, percent=percent, state=state,
            report_name=report_name, detail=detail, actor=actor,
            comment=cls._comment(stage=stage, state=state, statistics=statistics),
        )
        return {
            "stage": stage,
            "stage_label": WorkflowGateService.STAGE_LABELS.get(stage, stage),
            "output_id": str(output.pk),
            "feedback_id": str(event.id),
            "state": state,
            "created": created,
            "acceptance_score": percent,
            "statistics": statistics,
            "validation": validation,
            "evolvable": detail["evolvable"],
            "skill_version": output.skill_version.version if output.skill_version_id else "",
        }

    @staticmethod
    def _comment(*, stage: str, state: str, statistics: dict) -> str:
        return (
            f"{stage} 阶段确认报告（{'草稿' if state == REVIEW_STATE_DRAFT else '正式提交'}）："
            f"已审核 {statistics['已审核数']}/{statistics['原始项数']}，"
            f"采纳 {statistics['采纳数']}，修改后采纳 {statistics['修改后采纳数']}，"
            f"删除 {statistics['删除数']}"
        )

    @classmethod
    def latest(cls, output) -> dict | None:
        """该产出最近一次人工确认结论（供页面回显"上次填到哪了"）。"""
        if output is None:
            return None
        event = (
            FeedbackEvent.objects
            .filter(output=output, reason_code=REVIEW_REASON_CODE)
            .order_by("-occurred_at", "-created_at")
            .first()
        )
        if event is None:
            return None
        detail = event.detail or {}
        return {
            "feedback_id": str(event.id),
            "state": detail.get("state", ""),
            "report_name": detail.get("report_name", ""),
            "statistics": detail.get("statistics") or {},
            "blank_count": detail.get("blank_count", 0),
            "evolvable": bool(detail.get("evolvable")),
            "submitted_at": event.occurred_at.isoformat() if event.occurred_at else "",
            "actor": event.actor.username if event.actor_id else "",
            # T11：差异计算的输入。报告文件由人保管、可能离线流转，平台手上只有
            # 这一次上传解析出来的"填过的行"原文；不在这里带出来，差异服务就只能
            # 靠重新下载 Excel 才知道人改了什么。
            "human_rows": list(detail.get("human_rows") or []),
        }

    @classmethod
    def history(cls, skill, *, limit: int = 12) -> list[dict]:
        """按版本列出**已正式提交**的人工确认结论。

        只取 submitted：草稿是"还没审完"，把它画进版本对比图会让采纳率曲线出现
        一段由"人还没填完"造成的假低谷。
        """
        if skill is None:
            return []
        rows = (
            FeedbackEvent.objects
            .filter(skill_version__skill=skill, reason_code=REVIEW_REASON_CODE)
            .select_related("skill_version")
            .order_by("-occurred_at")[: limit * 2]
        )
        history = []
        for row in rows:
            detail = row.detail or {}
            if detail.get("state") != REVIEW_STATE_SUBMITTED:
                continue
            history.append({
                "version": row.skill_version.version if row.skill_version_id else "",
                "version_id": str(row.skill_version_id) if row.skill_version_id else "",
                "score": round((row.value or 0) * 100, 2),
                "statistics": detail.get("statistics") or {},
                "at": row.occurred_at.isoformat() if row.occurred_at else "",
            })
            if len(history) >= limit:
                break
        return history


class ReviewFeedbackEventStore:
    """确认报告反馈的落库口径（唯一入口）。

    单独拆出来是因为"更新已有记录"与"新建记录"在 ``FeedbackEvent`` 上的字段
    写法不同（``update_or_create`` 的 ``defaults`` 不会覆盖幂等键以外的判断），
    把这层收在一处，是避免"草稿转提交"时漏更新某个字段的第二道保险。
    """

    @staticmethod
    def upsert(
        *, output, stage: str, key: str, percent: float, state: str,
        report_name: str, detail: dict, actor, comment: str,
    ):
        defaults = {
            "project_id": output.project_id,
            "output": output,
            "trace": output.trace,
            "capability": output.capability,
            "capability_kind": "skill",
            "skill_version": output.skill_version,
            "signal": REVIEW_SIGNAL.get(state, "edited"),
            # ``value`` 与原「采纳率」口径一致（0–1 的原样采纳率）：
            # 版本对比图要能直接把两条流画在同一根轴上。
            "value": round(float(percent) / 100.0, 4),
            "reason_code": REVIEW_REASON_CODE,
            "comment": comment,
            "detail": detail,
            "actor": actor,
            "actor_type": "user",
        }
        event, created = FeedbackEvent.objects.get_or_create(
            idempotency_key=key, defaults=defaults,
        )
        if not created:
            # 草稿 → 正式提交是同一条记录的**状态推进**；不做这一步会让
            # "先存草稿再提交"在版本对比图上多出一个点，而它其实只发生过一次评审。
            #
            # ``occurred_at`` 刻意不动：它记的是"这次评审是什么时候做的"，
            # 重复上传同一份文件不该把评审时间往后推——那会让版本对比图上
            # 的点无端右移，看上去像又评了一轮。
            for field, value in defaults.items():
                setattr(event, field, value)
            event.save(update_fields=list(defaults))
        return event, created
