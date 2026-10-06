"""T14：飞轮登记失败的补偿队列（设计 §13 / R15）。

一句话职责：**把"业务已产出、飞轮没登记上"这件事变成一条可查、可重试、可告警的记录，
同时不让业务侧的任何东西因此丢失。**

为什么需要一个专门的服务而不是在 ``publish_output`` 里套个 ``try/except``：

- ``try/except + logger.exception`` 只解决了"不炸"，没解决"看得见"。上线后运维要回答
  的问题是"**有没有**产出没登记上"，而这个问题在日志里搜不出来——日志里只有出现的
  反例，没有"本该出现却缺席"的正例。必须有表。
- 重试要能回答"重试过几次、为什么进死信"。这需要 ``attempts`` / ``max_attempts`` /
  ``last_error`` 落在库里，而不是散在 worker 日志里。
- 幂等键必须是 (产出, 流程, 阶段)：一次网络抖动手动重试三次，控制台不能看到三条死信——
  告警数字一旦失真就再没人相信它。

判定职责边界：本模块**只**负责登记的成败与补偿，不重复实现门禁、锁与候选入队的逻辑
（那些是 ``WorkflowGateService`` 的职责）。登记成功时顺手关掉未决补偿记录；
登记失败时落一条补偿记录。
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .capability_registry import ALL_WORKFLOW_STAGES
from .workflow_models import (
    DEFAULT_REGISTRATION_MAX_ATTEMPTS,
    FlywheelRegistrationFailure,
    REGISTRATION_ALERT_STATES,
    REGISTRATION_DEAD_LETTER,
    REGISTRATION_FAILED,
    REGISTRATION_OPEN_STATES,
    REGISTRATION_PENDING,
    REGISTRATION_RESOLVED,
)

logger = logging.getLogger(__name__)

#: 单条错误摘要的截断长度。错误可能带整段 traceback，补偿记录要留证据但不必
#: 把日志搬进库；正文级的敏感信息更不该跟着错误串进观测面。
ERROR_SUMMARY_LIMIT = 500

#: 每条记录最多保留的尝试摘要条数。
HISTORY_LIMIT = 10

#: 产出不是受控流程产出时"没登记"的原因码。这不是失败，是"本来就不该登记"。
REASON_NOT_WORKFLOW_OUTPUT = "not_workflow_output"


def _summarize(error) -> str:
    text = str(error).strip() or error.__class__.__name__
    return text[:ERROR_SUMMARY_LIMIT]


class FlywheelRegistrationService:
    """正式产出 → 飞轮门禁的登记入口（带补偿）。"""

    # ------------------------------------------------------------ 登记

    @classmethod
    def registration_scope(cls, output) -> tuple[str, str]:
        """产出该登记到哪条链路的哪个阶段；不属于受控流程时返回 ``("", "")``。

        口径与 ``WorkflowGateService.register_output`` 保持一致：读产出协议里的
        ``workflow_id`` / ``stage``。**故意不查库补一个 workflow_id**——
        旁路产出协议里没有流程就是没有，替它猜一条会让"这份产出属于哪条链路"
        变成一个事后可改的结论。
        """
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = str(protocol.get("workflow_id") or "")
        stage = str(protocol.get("stage") or output.task_type or "")
        if not workflow_id or stage not in ALL_WORKFLOW_STAGES:
            return "", ""
        return workflow_id, stage

    @classmethod
    def register(cls, output, *, actor=None, create_gate: bool = True) -> dict:
        """登记一条正式产出；**失败不抛出**，转为补偿记录。

        返回 ``{"registered", "gate_id", "compensation_id", "reason", "error"}``。
        调用方据此把"业务成功"和"飞轮登记成功"分开呈现——这正是设计 §13 第一句
        的要求，也是 R15 的验收条件。

        ⚠️ 契约：本方法**绝不**因为飞轮侧的问题而抛异常。业务产出此时已经落库，
        让一个"账没记上"的问题反向炸掉业务的成功响应，等于用控制面的故障
        去宣告执行面的失败。
        """
        workflow_id, stage = cls.registration_scope(output)
        if not workflow_id:
            return {
                "registered": False, "gate_id": "", "compensation_id": "",
                "reason": REASON_NOT_WORKFLOW_OUTPUT, "error": "",
            }

        from .operations import WorkflowGateService

        try:
            gate = WorkflowGateService.register_output(output, create_gate=create_gate)
        except Exception as exc:  # noqa: BLE001 —— 见上面契约，此处必须兜住
            logger.warning(
                "飞轮登记失败，转入补偿队列：output=%s workflow=%s stage=%s error=%s",
                output.pk, workflow_id, stage, exc,
            )
            record = cls.record_failure(
                output=output, workflow_id=workflow_id, stage=stage,
                error=exc, actor=actor,
            )
            return {
                "registered": False, "gate_id": "",
                "compensation_id": str(record.pk),
                "reason": "registration_failed", "error": _summarize(exc),
            }

        cls.resolve_open(
            output=output, workflow_id=workflow_id, stage=stage,
            actor=actor, note="补登成功",
        )
        return {
            "registered": True,
            "gate_id": str(getattr(gate, "pk", "") or ""),
            "compensation_id": "", "reason": "", "error": "",
        }

    # ------------------------------------------------------------ 补偿记录

    @classmethod
    @transaction.atomic
    def record_failure(cls, *, output, workflow_id: str, stage: str, error,
                       actor=None, max_attempts: int | None = None):
        """落一条补偿记录（幂等）。

        幂等键 = (产出, 流程, 阶段)。同一产出的重复失败只**累加 attempts**：
        - 新记录：``attempts = 1``，按阈值决定 ``failed`` / ``dead_letter``；
        - 已有记录：``attempts += 1``，达到 ``max_attempts`` 转 ``dead_letter``；
        - 已 ``resolved`` 的记录再次失败：**重新打开**并继续累加——不新建一条，
          否则"这个产出的登记问题"会被拆成两条时间线，谁也无法回答它到底修好没有。
        """
        now = timezone.now()
        record, _created = (
            FlywheelRegistrationFailure.objects
            .select_for_update()
            .get_or_create(
                output=output, workflow_id=workflow_id, stage=stage,
                defaults={
                    "project": output.project,
                    "max_attempts": int(max_attempts or DEFAULT_REGISTRATION_MAX_ATTEMPTS),
                    "status": REGISTRATION_FAILED,
                },
            )
        )
        if max_attempts:
            record.max_attempts = int(max_attempts)
        record.attempts = int(record.attempts or 0) + 1
        summary = _summarize(error)
        record.last_error = summary
        record.history = [
            *(record.history or []),
            {"at": now.isoformat(), "attempt": record.attempts, "error": summary},
        ][-HISTORY_LIMIT:]
        ceiling = int(record.max_attempts or 1)
        record.status = REGISTRATION_DEAD_LETTER if record.attempts >= ceiling else REGISTRATION_FAILED
        record.resolved_at = None
        record.resolved_by = None
        record.save(update_fields=[
            "attempts", "max_attempts", "last_error", "history", "status",
            "resolved_at", "resolved_by", "updated_at",
        ])
        if record.status == REGISTRATION_DEAD_LETTER:
            # 死信必须留下一条**可被告警系统抓取**的记录。这里用 error 级别，
            # 与候选队列保持一致；真正的告警由控制台读 status_summary 触发，
            # 不依赖日志系统——日志会滚动，状态不会。
            logger.error(
                "飞轮登记进入死信，需人工介入：project=%s output=%s workflow=%s stage=%s attempts=%s",
                record.project_id, output.pk, workflow_id, stage, record.attempts,
            )
        return record

    @classmethod
    def resolve_open(cls, *, output, workflow_id: str, stage: str,
                     actor=None, note: str = "") -> int:
        """登记成功后关掉该产出该阶段的未决补偿记录。返回关闭条数。

        为什么成功时要主动关：否则一条已经被后续重试修好的记录会永远停在
        ``failed``，控制台的"待处理"计数就再也不会归零——运维很快会学会忽略它。
        """
        rows = FlywheelRegistrationFailure.objects.filter(
            output=output, workflow_id=workflow_id, stage=stage,
            status__in=list(REGISTRATION_OPEN_STATES),
        )
        count = rows.count()
        if not count:
            return 0
        rows.update(
            status=REGISTRATION_RESOLVED, resolved_at=timezone.now(),
            resolved_by=actor, updated_at=timezone.now(),
        )
        if note:
            # update() 不触发 save()，逐条补一次 history 以留下"谁在什么时候解决的"。
            for record in FlywheelRegistrationFailure.objects.filter(
                output=output, workflow_id=workflow_id, stage=stage,
                status=REGISTRATION_RESOLVED,
            ):
                record.history = [
                    *(record.history or []),
                    {"at": timezone.now().isoformat(), "attempt": record.attempts, "note": note},
                ][-HISTORY_LIMIT:]
                record.save(update_fields=["history", "updated_at"])
        return count

    # ------------------------------------------------------------ 重试与查询

    @classmethod
    def retry(cls, record, *, actor=None):
        """重试一条补偿记录。

        实现上直接复用 ``register``：登记成功会自动 ``resolve_open`` 关闭未决记录，
        失败会累加 ``attempts``。**不另写一条"重试专用"的写入路径**——两条路径
        迟早会在"什么时候算成功"上分叉。
        """
        if record is None:
            return None
        if record.status == REGISTRATION_RESOLVED:
            return record
        cls.register(record.output, actor=actor)
        record.refresh_from_db()
        return record

    @classmethod
    def retry_failed(cls, *, project_id, statuses=REGISTRATION_OPEN_STATES,
                     limit: int = 50, actor=None) -> list:
        ids = list(
            FlywheelRegistrationFailure.objects
            .filter(project_id=project_id, status__in=list(statuses))
            .order_by("created_at").values_list("pk", flat=True)[:limit]
        )
        results = []
        for pk in ids:
            record = FlywheelRegistrationFailure.objects.filter(pk=pk).first()
            retried = cls.retry(record, actor=actor)
            results.append({
                "id": str(pk),
                "status": getattr(retried, "status", "missing"),
                "attempts": getattr(retried, "attempts", 0),
            })
        return results

    @classmethod
    def status_summary(cls, project_id) -> dict:
        """控制台用的补偿队列概览。``alert`` 只看死信，不看 ``failed``。"""
        from django.db.models import Count

        rows = (
            FlywheelRegistrationFailure.objects.filter(project_id=project_id)
            .values("status").annotate(total=Count("id"))
        )
        counts = {row["status"]: row["total"] for row in rows}
        dead = counts.get(REGISTRATION_DEAD_LETTER, 0)
        failed = counts.get(REGISTRATION_FAILED, 0)
        pending = counts.get(REGISTRATION_PENDING, 0)
        return {
            "project_id": project_id,
            "by_status": counts,
            "pending": pending,
            "failed": failed,
            "dead_letter": dead,
            "resolved": counts.get(REGISTRATION_RESOLVED, 0),
            "open": pending + failed,
            "alert": dead > 0,
        }

    @classmethod
    def latest_for(cls, output):
        """某产出的最新补偿记录（无则 None）——业务页据此提示"这场产出没进飞轮"。"""
        return (
            FlywheelRegistrationFailure.objects.filter(output=output)
            .order_by("-created_at").first()
        )

    @classmethod
    def open_for_project(cls, project_id):
        """未决补偿记录（供运维逐条看，而不是只看计数）。"""
        return (
            FlywheelRegistrationFailure.objects
            .filter(project_id=project_id, status__in=list(REGISTRATION_OPEN_STATES))
            .select_related("output", "project").order_by("created_at")
        )

    #: 供 ``rollout`` 等模块引用，避免各处再写一遍集合。
    ALERT_STATES = REGISTRATION_ALERT_STATES
    OPEN_STATES = REGISTRATION_OPEN_STATES
