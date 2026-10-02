"""Skill 运行时版本解析与任务级锁（T08 / R4、R8）。

两条路径必须严格分开，否则"新版本激活不改变运行中任务"就无从保证：

- **新建任务**（``resolve_version`` / ``lock_for_task``）：必须解析到**当前 active**
  的版本，找不到就拒绝启动。任何 ``draft``/``shadow``/``quarantined``/已退役版本
  都不可用——这正是 R13「无活跃版本拒绝执行」的实际拦截点。
- **已启动任务**（``resolve_locked``）：只认任务启动时固化的那把锁，**不重新解析**。
  该版本哪怕已经被新版本取代（``retired``）也照样继续跑，因为中断一条跑到一半的
  流水线，其破坏性远大于让它用旧包跑完。唯一的例外是版本被 ``quarantined``
  （安全事件），此时必须终止。

解析性能目标 P95 < 50ms：活跃版本走 ``Skill.active_version`` 指针 + ``select_related``
一次查询拿到，不重算包哈希（那是文件 IO，放在显式的 ``verify_integrity`` 开关后面）。
"""
from __future__ import annotations

import logging
import time

from django.core.exceptions import ValidationError
from django.db import transaction

from knowledge_evolution.workflow_models import WorkflowSkillLock

from .models import Skill, SkillVersion

logger = logging.getLogger(__name__)

#: 解析耗时超过这个毫秒数就记一条 warning，便于观察"解析变慢"而不是等到超时。
SLOW_RESOLVE_MS = 50.0


class SkillRuntimeUnavailable(ValidationError):
    """没有可用的活跃 Skill 版本，或锁定版本已不可继续使用。

    继承 ``ValidationError`` 是为了让上层视图/服务能按同一种方式处理：
    这是**可预期的业务拒绝**（应当回 4xx 并提示负责人去激活版本），
    不是服务端故障。抛出它不会写入任何业务状态。
    """


class SkillRuntimeResolver:
    """按项目 + 能力/阶段解析活跃 Skill 版本，并为任务固化版本锁。"""

    # ------------------------------------------------------------ 新建任务

    @staticmethod
    def lock_key_for(*, stage: str = "", capability=None, name: str = "", skill=None) -> str:
        """计算锁的定位键：同一个 workflow 里不同阶段各占一把锁。"""
        if stage:
            return f"stage:{stage}"
        if capability is not None:
            return f"capability:{getattr(capability, 'pk', capability)}"
        if skill is not None:
            return f"skill:{getattr(skill, 'pk', skill)}"
        if name:
            return f"skill:{name}"
        return "default"

    @classmethod
    def resolve_version(
        cls, *, project, capability=None, stage: str = "", name: str = "",
        skill=None, verify_integrity: bool = False,
        allow_stage_mismatch: bool = False,
    ) -> SkillVersion | None:
        """解析当前应当使用的活跃版本；没有可用版本时返回 None。

        只返回**真正可运行**的版本：发布状态为 ``active``、Skill 本身处于启用、
        属于指定项目，且（给了 ``stage`` 时）manifest 声明的阶段匹配。

        Args:
            skill: 直接指定要解析的 Skill（单能力任务通常已经知道自己在用哪个 Skill，
                例如用例审查会带上用户选中的那个）。给了它就不再按名称/阶段去猜，
                避免"同名 Skill 有多条时解析到另一条"这种静默错误。
            allow_stage_mismatch: 显式选择与 manifest 声明不一致时是否放行。
                默认 False——"声明了别的阶段"是个信号，不该被静默忽略。
                只有**人明确为某个阶段指定了包**（发起流程向导）时才置 True：
                此时"人选了它"这件事本身优先于包里的默认声明，但仍须是
                ``active`` 版本，R13「无活跃版本拒绝执行」不因这条放宽而失效。
        """
        started = time.perf_counter()
        project_id = getattr(project, "pk", project)

        queryset = (
            Skill.objects
            .filter(project_id=project_id, is_active=True, active_version__isnull=False)
            .select_related("active_version", "active_version__release", "capability")
        )
        if skill is not None:
            queryset = queryset.filter(pk=getattr(skill, "pk", skill))
        if name:
            queryset = queryset.filter(name=name)
        if capability is not None:
            queryset = queryset.filter(capability_id=getattr(capability, "pk", capability))
        if stage and not (skill is not None and allow_stage_mismatch):
            # 交给数据库过滤：Postgres 会把 manifest->>'stage' 下推到 JSON 索引，
            # 比取回全部活跃 Skill 再在 Python 里筛要稳得多。
            queryset = queryset.filter(active_version__manifest__stage=stage)

        resolved = None
        for skill in queryset:
            version = skill.active_version
            if version is None:
                continue
            # 指针是快速路径，权威状态仍在发布单元上；两者不一致时以发布单元为准。
            if version.release_id and version.release.state != "active":
                logger.warning(
                    "活跃指针与发布状态不一致，已跳过：skill=%s version=%s release_state=%s",
                    skill.name, version.version, version.release.state,
                )
                continue
            if version.skill.project_id != project_id:
                continue
            resolved = version
            break

        if resolved is not None and verify_integrity:
            cls._assert_integrity(resolved)

        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > SLOW_RESOLVE_MS:
            logger.warning("Skill 版本解析耗时 %.1fms（目标 <%.0fms）", elapsed_ms, SLOW_RESOLVE_MS)
        return resolved

    @classmethod
    def require_version(cls, **kwargs) -> SkillVersion:
        """"解析活跃版本，解析不到就拒绝——新建任务的强制入口。"""
        version = cls.resolve_version(**kwargs)
        if version is None:
            raise SkillRuntimeUnavailable(cls._missing_message(**kwargs))
        return version

    @classmethod
    @transaction.atomic
    def lock_for_task(
        cls, *, project, workflow_id, actor=None, scope: str = "single",
        stage: str = "", capability=None, name: str = "", skill=None,
        verify_integrity: bool = False, allow_missing: bool = False,
        allow_stage_mismatch: bool = False,
    ) -> WorkflowSkillLock | None:
        """为任务固化 Skill 版本，返回 ``WorkflowSkillLock``。

        幂等：同一个 ``(project, workflow_id, lock_key)`` 只会锁一次。任务中途重入
        （重试、补偿、恢复）拿到的仍是第一次锁定的版本，不会因为期间有人激活了新
        版本而换包。

        Args:
            skill: 直接指定要锁定的 Skill（见 ``resolve_version``）。
            allow_missing: 为 True 时，解析不到活跃版本返回 None 而不是拒绝。
                只有"该能力本来就可以没有 Skill"的场景才该打开它。
            allow_stage_mismatch: 见 ``resolve_version``。仅在"人显式指定了包"的
                入口打开。
        """
        if not workflow_id:
            raise ValidationError("锁定 Skill 版本必须提供 workflow_id")

        project_id = getattr(project, "pk", project)
        lock_key = cls.lock_key_for(stage=stage, capability=capability, name=name, skill=skill)

        existing = (
            WorkflowSkillLock.objects
            .filter(project_id=project_id, workflow_id=str(workflow_id), lock_key=lock_key)
            .select_related("skill", "skill_version", "release")
            .first()
        )
        if existing is not None:
            return existing

        version = cls.resolve_version(
            project=project, capability=capability, stage=stage, name=name, skill=skill,
            verify_integrity=verify_integrity, allow_stage_mismatch=allow_stage_mismatch,
        )
        if version is None:
            if allow_missing:
                return None
            raise SkillRuntimeUnavailable(
                cls._missing_message(
                    project=project, capability=capability, stage=stage, name=name, skill=skill,
                )
            )

        declared_stage = str((version.manifest or {}).get("stage") or "")
        lock, _ = WorkflowSkillLock.objects.get_or_create(
            project_id=project_id, workflow_id=str(workflow_id), lock_key=lock_key,
            defaults={
                "scope": scope,
                "stage": stage or "",
                "capability_id": getattr(capability, "pk", capability),
                "skill_id": version.skill_id,
                "skill_version": version,
                "release_id": version.release_id,
                "package_sha256": version.package_sha256,
                "locked_by": actor if getattr(actor, "pk", None) else None,
                "detail": {
                    "resolved_version": version.version,
                    "resolved_state": version.state,
                    "source_type": version.source_type,
                    # 人显式指定的包若与 manifest 声明不一致，必须留下证据：
                    # 否则事后只看到"这一阶段用了这个包"，看不出当时是**违反声明**用的。
                    "pinned": bool(skill is not None),
                    "declared_stage": declared_stage,
                    "stage_mismatch": bool(
                        skill is not None and stage and declared_stage and declared_stage != stage
                    ),
                },
            },
        )
        logger.info(
            "任务锁定 Skill 版本：workflow=%s key=%s skill=%s version=%s",
            workflow_id, lock_key, version.skill.name, version.version,
        )
        return lock

    # ------------------------------------------------------------ 已启动任务

    @classmethod
    def resolve_locked(cls, lock, *, verify_integrity: bool = False) -> SkillVersion:
        """取回锁定版本；**不重新解析活跃版本**。

        容忍该版本已被取代（``retired``）——运行中的任务继续跑完比中途换包更安全。
        拒绝的情形只有三种：版本记录被清理、跨项目、以及被隔离。

        这里刻意**重新查库**而不是直接用 ``lock.skill_version``：那把锁可能在任务
        更早的阶段就被取出并缓存，而版本随后被隔离（安全事件）——读缓存对象会看到
        过期的 ``active`` 状态，把已经不该跑的任务放过去。
        """
        version = None
        if lock.skill_version_id:
            version = (
                SkillVersion.objects
                .select_related("release", "skill")
                .filter(pk=lock.skill_version_id)
                .first()
            )
        if version is None:
            raise SkillRuntimeUnavailable("锁定的 Skill 版本已被清理，任务无法继续")
        if version.skill.project_id != lock.project_id:
            raise SkillRuntimeUnavailable("锁定的 Skill 版本不属于当前项目")
        # 隔离是安全事件，必须让任务停下来；retired 不属于此列。
        if version.state == "quarantined":
            raise SkillRuntimeUnavailable("锁定的 Skill 版本已被隔离，任务必须终止并重建")
        if lock.package_sha256 and version.package_sha256 != lock.package_sha256:
            raise SkillRuntimeUnavailable("锁定的版本包哈希与当前记录不一致，疑似版本被改写")
        if verify_integrity:
            cls._assert_integrity(version)
        return version

    @classmethod
    def locks_for_workflow(cls, *, project, workflow_id) -> list:
        """列出某个任务的全部版本锁（四阶段会有多条）。"""
        return list(
            WorkflowSkillLock.objects
            .filter(project_id=getattr(project, "pk", project), workflow_id=str(workflow_id))
            .select_related("skill", "skill_version", "capability")
            .order_by("locked_at")
        )

    # ------------------------------------------------------------ 辅助

    @staticmethod
    def _assert_integrity(version) -> None:
        """校验磁盘包未被改写（容忍平台注入的 API Key）。"""
        from .versions import SkillVersionService

        check = SkillVersionService.verify_package_integrity(version)
        if not check["ok"]:
            raise SkillRuntimeUnavailable(
                f"Skill 包完整性校验失败（{check['reason']}）："
                f"{version.skill.name}@{version.version}"
            )

    @staticmethod
    def _missing_message(*, project=None, capability=None, stage: str = "", name: str = "",
                         skill=None) -> str:
        if skill is not None:
            target = getattr(skill, "name", "") or str(getattr(skill, "pk", ""))
        else:
            target = (
                name
                or (getattr(capability, "name", "") if capability is not None else "")
                or stage or "默认能力"
            )
        project_name = getattr(project, "name", "") if project is not None else ""
        prefix = f"项目「{project_name}」" if project_name else "当前项目"
        return (
            f"{prefix}没有可运行的活跃 Skill 版本（{target}）。"
            "请由测试负责人完成版本激活后再启动任务。"
        )
