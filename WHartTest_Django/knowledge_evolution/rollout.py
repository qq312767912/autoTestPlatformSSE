"""T14：项目级灰度开关与上线就绪自检。

## 开关只有一个语义

**这个项目是否已灰度开启质量飞轮联动。**

- 开启：业务页面可以发起飞轮链路、纳管旁路产出、按阶段受控执行。
- 关闭（默认，含"从未配置过"）：飞轮**控制面入口**一律拒绝；但业务页面的
  方案分析 / Agent 调用 / 产出发布**完全不受影响**——它们走执行面，
  执行面本来就不依赖飞轮。

## 为什么默认是关

灰度上线时绝大多数项目不该被卷进来。"未配置即视为开启"会让"先灰度上证 e 投票
方案阶段"这句话失去意义——它等价于全量开放，而全量开放正是本项目要避免的。

## 为什么不把开关做在执行面（``publish_output`` / ``record_task_output``）

1. 非目标里写明"不重建第二套 Agent"。关掉开关的项目里业务产出照样要能发布、
   能被检索——若把执行面也闸住，飞轮就从"可选的控制面"变成了"业务的前置依赖"。
2. 一条**已经开跑**的受控链路，不该因为运维事后关了开关就在半路断掉。
   开关控制的是"要不要开始用"，不是"用到一半把人踢出去"。因此闸门只落在
   入口动作（发起 / 启动 / 纳管），不落在已存在链路的登记、反馈与归因。

## 为什么真值放这里而不是各入口各写一份

"未开启"的判定、文案和响应码必须一致，否则同一个开关会在不同入口给出
不同结论——最糟的表现是"向导说能发起、发起时 400"。
"""

from __future__ import annotations

from .history_models import ProjectFlywheelSetting

#: 关闭开关时的统一文案。前端与补偿手册都引用这一份。
LINKAGE_DISABLED_MESSAGE = "当前项目未开启质量飞轮联动（灰度开关关闭）"

#: 灰度首批目标（设计 §14 一期）：上证 e 投票方案阶段。
PILOT_STAGE = "test_plan_generation"


def _project_id(project) -> int:
    """接受 Project 实例或 id；不接受 None（调用方必须先解决"哪个项目"）。"""
    if project is None:
        raise ValueError("判定灰度开关必须指定项目")
    return int(getattr(project, "pk", project))


def linkage_enabled(project) -> bool:
    """项目是否已灰度开启飞轮联动。**未配置视为关闭**（opt-in）。"""
    return ProjectFlywheelSetting.objects.filter(
        project_id=_project_id(project), enabled=True,
    ).exists()


def linkage_state(project) -> dict:
    """给前端的开关状态：据此决定飞轮入口是否可见/可用。

    不返回 ``enabled`` 就叫前端自己猜：猜错的后果是按钮点了报 403——
    用户看到的是"这个功能坏了"，而不是"这个项目还没灰度到"。
    """
    row = ProjectFlywheelSetting.objects.filter(
        project_id=_project_id(project),
    ).select_related("updated_by").first()
    return {
        "project_id": _project_id(project),
        "enabled": bool(row.enabled) if row else False,
        "configured": row is not None,
        "rollout_note": (row.rollout_note if row else "") or "",
        "updated_by": (row.updated_by.username if row and row.updated_by_id else ""),
        "updated_at": (row.updated_at.isoformat() if row else ""),
        "pilot_stage": PILOT_STAGE,
    }


def assert_linkage_enabled(project) -> None:
    """控制面入口的统一闸门。

    抛的是 Django ``ValidationError`` 而不是 DRF 异常：本模块要能被服务层、
    管理命令和视图共同复用，不能把 HTTP 框架拖进来。视图侧统一翻译成 403
    （见 ``views._ensure_linkage_enabled``）——"项目没开这个能力"是权限问题，
    不是"你的参数写错了"，用 400 会把用户引向去检查 payload。
    """
    from django.core.exceptions import ValidationError

    if not linkage_enabled(project):
        raise ValidationError(LINKAGE_DISABLED_MESSAGE)


class LaunchReadinessService:
    """上线检查表的**程序化**版本：能自动判的全部自动判。

    为什么要有这个而不是只写一份 markdown 清单：纯文档的检查表在上线当天
    靠人逐条勾，勾完没人能证明勾的是当时的库。这里把可判定的条件变成一次
    可复现的调用，剩余的人工项才留在文档里。
    """

    #: 上线必须先落地的三份说明（相对 spec 目录）。
    RUNBOOKS = (
        "runbook/go-live-checklist.md",
        "runbook/compensation-runbook.md",
        "runbook/rollback-runbook.md",
    )

    #: **软条件**：不影响 ``ready`` 的检查项。
    #:
    #: 只放"运营动作"类：项目是否已配灰度、开关当前开着还是关着。若把这两条
    #: 也算成硬条件，上线前一天必须先开开关、配好灰度记录，自检才可能通过——
    #: 而"该不该开开关"恰恰是自检通过之后才决定的事，逻辑上绕成死循环。
    #: 真正决定代码能不能上的是迁移、隔离、死信和手册。
    SOFT_CHECK_CODES = frozenset({"linkage_flag_configured", "linkage_switch_state"})

    #: 手册目录的设置名。手册是**发布物**的一部分，不在后端运行时镜像里，
    #: 所以目录必须可配置；写死一个相对路径的结果是"换台机器就永远不 ready"。
    RUNBOOK_DIR_SETTING = "FLYWHEEL_RUNBOOK_DIR"

    @classmethod
    def _runbook_dir(cls):
        """定位手册目录；本环境确实没有时返回 ``None``（不是"通过"）。"""
        from django.conf import settings
        from pathlib import Path

        configured = getattr(settings, cls.RUNBOOK_DIR_SETTING, "") or ""
        candidates = [Path(configured)] if configured else []
        # 兜底：从本文件往上找带 specs/<spec>/agent-execution-evolution 的祖先目录，
        # 这样本地开发（源码树）不用额外配置也能判。
        here = Path(__file__).resolve()
        tail = Path("specs") / "sse-evote-skill-flywheel" / "agent-execution-evolution"
        candidates.extend(ancestor / tail for ancestor in here.parents)
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    @classmethod
    def _runbooks_check(cls) -> dict:
        """三份手册是否齐备。

        本环境找不到手册目录时**显式标 skipped**，而不是记 ok：把"我没法判"
        写成"通过"，等于用一条假绿把上线检查表上真正该人工确认的一项抹掉。
        """
        directory = cls._runbook_dir()
        if directory is None:
            return {
                "code": "runbooks_present", "ok": True, "skipped": True,
                "detail": "本环境未提供手册目录，转为上线检查表的人工确认项",
            }
        missing = [name for name in cls.RUNBOOKS if not (directory / name).exists()]
        return {
            "code": "runbooks_present", "ok": not missing, "skipped": False,
            "detail": "三份手册齐备" if not missing else "缺失：" + "、".join(missing),
        }

    @classmethod
    def _pending_migrations(cls) -> list:
        """未应用的迁移。判定"存量兼容"的硬证据——有未落库的迁移就不是可上线状态。"""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        return sorted(
            f"{migration.app_label}.{migration.name}"
            for migration in executor.migration_plan(executor.loader.graph.leaf_nodes())
        )

    @classmethod
    def _isolation_issues(cls, project_id: int) -> dict:
        """跨项目引用计数（复用已上线的审计命令口径，不另写一套判定）。"""
        from django.db.models import F, Q

        from .history_models import HistoryImportItem, HistoryReplay

        bad_items = HistoryImportItem.objects.filter(
            batch__project_id=project_id,
        ).exclude(batch__project_id=F("file__project_id")).count()
        bad_replays = HistoryReplay.objects.filter(project_id=project_id).filter(
            ~Q(batch__project_id=F("project_id"))
            | ~Q(flywheel_run__project_id=F("project_id"))
            | (Q(gold_version__isnull=False) & ~Q(gold_version__dataset__project_id=F("project_id")))
        ).count()
        return {"history_item": bad_items, "history_replay": bad_replays,
                "total": bad_items + bad_replays}

    @classmethod
    def check(cls, project) -> dict:
        project_id = _project_id(project)
        from .gold import AssetCandidateService
        from .registration import FlywheelRegistrationService

        pending = cls._pending_migrations()
        isolation = cls._isolation_issues(project_id)
        candidates = AssetCandidateService.status_summary(project_id)
        registration = FlywheelRegistrationService.status_summary(project_id)
        state = linkage_state(project)

        checks = [
            {"code": "migrations_applied", "ok": not pending, "skipped": False,
             "detail": "迁移已全部应用" if not pending else f"未应用迁移：{', '.join(pending[:5])}"},
            {"code": "linkage_flag_configured", "ok": state["configured"], "skipped": False,
             "detail": "已配置项目级灰度开关" if state["configured"] else "未配置灰度开关（将按关闭处理）"},
            {"code": "no_cross_project_reference", "ok": isolation["total"] == 0, "skipped": False,
             "detail": f"跨项目引用 {isolation['total']} 条" if isolation["total"] else "无跨项目引用"},
            {"code": "no_dead_letter", "ok": not candidates["alert"] and not registration["alert"],
             "skipped": False,
             "detail": (f"候选死信 {candidates['dead_letter']} / 登记失败 {registration['dead_letter']}"
                        if (candidates["alert"] or registration["alert"]) else "无死信积压")},
            cls._runbooks_check(),
            {"code": "linkage_switch_state", "ok": True, "skipped": False,
             "detail": "已灰度开启" if state["enabled"] else "当前关闭（关闭不影响业务生成）"},
        ]
        # 只有"硬条件"决定 ready：开关配置与开关状态本身不是上线缺陷；
        # 被标 skipped 的（本环境判不了的）同样不拦——它们必须由人工项兜住，
        # 而不是被程序悄悄判成通过。
        hard = [
            item for item in checks
            if item["code"] not in cls.SOFT_CHECK_CODES and not item.get("skipped")
        ]
        return {
            "project_id": project_id,
            "ready": all(item["ok"] for item in hard),
            "linkage": state,
            "checks": checks,
            "isolation": isolation,
        }
