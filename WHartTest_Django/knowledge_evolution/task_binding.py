"""T14：业务任务与 Skill 版本 / 发布单元的绑定层。

这一层回答一个具体问题：**一次真实业务任务，到底用的是哪一份能力包？**

为什么不把这件事丢给各业务自己写：

- 用例审查（T14）、四阶段流水线（T15）、代码审查复合能力（T17）三个场景的
  "锁定时机"不同（单能力任务锁一次；四阶段每阶段一把锁；复合能力锁整个 Release），
  但"锁定之后必须一路带着走"这件事完全一样。各写一遍的结果必然是某一条链路漏带，
  表现为"产出能生成、反馈也能回流，但金标候选里的 `package_sha256` 是空的"——
  闭环看着跑通了，归因却定位不到版本。
- R13 要求"无可用版本拒绝执行"。这个拒绝点必须和锁定点在同一个事务里，
  否则会出现"锁定失败但任务已经开跑"的半启动状态。

绑定结果一律通过 ``OutputEnvelope.skill_version`` 进入产出协议，再由
``GenerationOutput.skill_version`` / ``skill_package_sha256`` 落库；反馈、金标、
归因、派生全部从这里往下读。
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError

from skills.runtime import SkillRuntimeResolver, SkillRuntimeUnavailable

logger = logging.getLogger(__name__)

#: 用例审查能力的标识（与 ``capability_registry`` 的 ``case_review`` 对齐）。
CASE_REVIEW_STAGE = "case_review"

#: 平台内置用例审查 Skill 的默认名称。项目里登记了同名 Skill 时以登记的那条为准。
DEFAULT_CASE_REVIEW_SKILL_NAME = "test-case-clarity-review"

#: 未纳入版本管理时的说明文本。写出来是为了让"这次审查没有版本溯源"成为
#: 一个**可读的结论**，而不是让下游看到空字符串去猜。
UNMANAGED_DETAIL = (
    "项目内未登记用例审查 Skill，本次审查使用平台内置规则，未纳入版本管理"
    "（产出不会有可回滚的 Skill 版本）。"
)


class SkillBindingRefused(ValidationError):
    """拒绝启动任务：存在候选能力包但没有任何可运行的活跃版本。

    继承 ``ValidationError`` → 上层按"可预期的业务拒绝"处理（回 4xx/标记任务失败
    并给出提示），而不是当服务端故障。抛出它**不会**写入任何业务状态。
    """


class TaskSkillBindingService:
    """把业务任务绑定到具体的 Skill 版本。"""

    # ------------------------------------------------------------ 用例审查（T14）

    @classmethod
    def bind_case_review(cls, *, review, actor=None, skill=None) -> dict:
        """锁定并返回本次用例审查使用的 Skill 版本。

        三种结局，必须区分开（这是本方法存在的全部意义）：

        1. **项目登记了用例审查 Skill，且有活跃版本** → 锁定并返回版本，产出可溯源。
        2. **项目登记了该 Skill，但没有活跃版本**（候选未审批、被隔离、被退役）
           → 抛 ``SkillBindingRefused``，**拒绝启动**。这是 R13 的实际拦截点：
           未经验证的候选包绝不能因为"反正没人拦"就跑进生产审查。
        3. **项目根本没有登记该 Skill** → 返回未绑定结果，审查回落到平台内置规则。
           这种情况不拒绝，因为拒绝的后果是"该项目的用例审查功能整体不可用"，
           而风险面并没有扩大——它本来就在用内置规则。

        Args:
            skill: 用户在本次审查中显式选中的 Skill。给了它就以它为准，
                不再按名称去猜（同名多条时猜错会静默用错包）。
        """
        from skills.models import Skill

        project = review.project
        project_id = getattr(project, "pk", project)
        workflow_id = str(review.pk)
        candidate = skill if skill is not None else getattr(review, "selected_skill", None)

        if candidate is None:
            candidate = (
                Skill.objects
                .filter(project_id=project_id, name=DEFAULT_CASE_REVIEW_SKILL_NAME)
                .first()
            )
        if candidate is None:
            return {
                "lock": None, "skill_version": None, "managed": False,
                "detail": UNMANAGED_DETAIL,
            }

        try:
            lock = SkillRuntimeResolver.lock_for_task(
                project=project, workflow_id=workflow_id,
                actor=actor if getattr(actor, "pk", None) else None,
                scope="single", stage="", skill=candidate,
            )
        except SkillRuntimeUnavailable as exc:
            raise SkillBindingRefused(
                f"用例审查 Skill 当前没有可运行的活跃版本，已拒绝启动审查：{exc}"
            ) from exc

        version = SkillRuntimeResolver.resolve_locked(lock)
        logger.info(
            "用例审查 %s 绑定 Skill 版本：%s@%s（lock=%s）",
            review.pk, version.skill.name, version.version, lock.id,
        )
        return {
            "lock": lock,
            "skill_version": version,
            "managed": True,
            "detail": (
                f"本次审查绑定 Skill「{version.skill.name}」版本 {version.version}"
                f"（包哈希 {version.package_sha256[:12]}）。"
            ),
        }

    # ------------------------------------------------------------ 四阶段流水线（T15）

    @classmethod
    def bind_stage(cls, *, project, workflow_id, stage: str, actor=None,
                   allow_unmanaged: bool = True, skill=None,
                   allow_stage_mismatch: bool = False) -> dict:
        """锁定四阶段流水线中**某一个阶段**的 Skill 版本。

        与 ``bind_case_review`` 用同一套三分支策略（见该方法的说明），只是把
        "项目是否登记了这个能力的 Skill"换成"项目是否登记了声明该阶段的 Skill"。

        Args:
            allow_unmanaged: 项目完全没登记该阶段的 Skill 时是否放行。
                四阶段流水线默认放行：一个刚开始接入的能力包通常只覆盖其中一两个
                阶段，硬拒绝会让整条链路不可用，而风险面并没有扩大——它本来就在用
                平台默认行为。但**登记了却没有可用活跃版本**时一律拒绝，
                两种情况不能混为一谈。
            skill: 人在发起流程时**为这个阶段显式选中的包**。给了它就以它为准：
                - 不再按 manifest 声明去筛阶段（见 ``allow_stage_mismatch``）；
                - 不再要求"项目登记过声明该阶段的包"——显式选择本身就是登记行为。
                仍然必须是 ``active`` 版本，绑不到就拒绝，不放行。
            allow_stage_mismatch: 显式选中的包声明的阶段与所选阶段不一致时是否放行。
                True 时把"声明了什么"与"实际用在哪个阶段"一起写进锁的 ``detail``，
                保证事后能看出这是**人主动跨声明使用**，而不是系统静默用错包。
        """
        from django.db.models import Q

        from skills.models import Skill

        project_id = getattr(project, "pk", project)
        # "登记过"要按**任意版本**的 manifest 判断，不能只看活跃版本：
        # 只看活跃版本会把"有候选但还没审批"错判成"根本没登记"，于是静默放行。
        if skill is not None:
            # 显式指定了包，就不再按声明挑：否则"新链路阶段还没有包声明它"会把
            # 人选好的包直接判成"未登记"，回落到无版本溯源的默认行为——
            # 这与用户的意图正好相反。
            registered = Skill.objects.filter(project_id=project_id, pk=getattr(skill, "pk", skill))
        else:
            registered = Skill.objects.filter(
                Q(versions__manifest__stage=stage) | Q(capability__stages__contains=[stage]),
                project_id=project_id,
            ).distinct()
        if not registered.exists():
            if not allow_unmanaged:
                raise SkillBindingRefused(
                    f"项目未登记阶段 {stage} 的 Skill，无法启动四阶段流水线"
                )
            return {
                "lock": None, "skill_version": None, "managed": False, "stage": stage,
                "detail": f"阶段 {stage} 未登记 Skill，该阶段使用平台默认行为（无版本溯源）。",
            }

        try:
            lock = SkillRuntimeResolver.lock_for_task(
                project=project, workflow_id=str(workflow_id),
                actor=actor if getattr(actor, "pk", None) else None,
                scope="workflow", stage=stage, skill=skill,
                allow_stage_mismatch=allow_stage_mismatch,
            )
        except SkillRuntimeUnavailable as exc:
            raise SkillBindingRefused(
                f"阶段 {stage} 的 Skill 没有可运行的活跃版本，已拒绝进入该阶段：{exc}"
            ) from exc

        version = SkillRuntimeResolver.resolve_locked(lock)
        lock_detail = lock.detail or {}
        pinned_note = ""
        if lock_detail.get("stage_mismatch"):
            # 跨声明使用必须写在返回值里，页面才能提示"这个包声明的是另一个阶段"。
            pinned_note = (
                f"⚠ 该包 manifest 声明的阶段是「{lock_detail.get('declared_stage')}」，"
                f"本次由人工指定用于「{stage}」。"
            )
        return {
            "lock": lock,
            "skill_version": version,
            "managed": True,
            "stage": stage,
            "pinned": bool(lock_detail.get("pinned")),
            "declared_stage": str(lock_detail.get("declared_stage") or ""),
            "stage_mismatch": bool(lock_detail.get("stage_mismatch")),
            "detail": (
                f"阶段 {stage} 绑定 Skill「{version.skill.name}」版本 {version.version}"
                f"（包哈希 {version.package_sha256[:12]}）。{pinned_note}"
            ),
        }

    # ------------------------------------------------------------ 查询

    @classmethod
    def binding_for_workflow(cls, *, project, workflow_id: str) -> dict:
        """回看某次任务当时锁定的版本（供页面与闭环排查使用）。

        允许锁定的版本此刻已被退役——"当时用的哪一份"是历史事实，
        不随当前发布状态改变。
        """
        locks = SkillRuntimeResolver.locks_for_workflow(
            project=project, workflow_id=str(workflow_id),
        )
        return {
            "workflow_id": str(workflow_id),
            "managed": bool(locks),
            "locks": [
                {
                    "id": str(lock.id),
                    "lock_key": lock.lock_key,
                    "scope": lock.scope,
                    "stage": lock.stage,
                    "skill_id": str(lock.skill_id or ""),
                    "skill_name": lock.skill.name if lock.skill_id else "",
                    "skill_version_id": str(lock.skill_version_id or ""),
                    "version": lock.skill_version.version if lock.skill_version_id else "",
                    "release_state": (
                        lock.release.state if lock.release_id else ""
                    ),
                    "package_sha256": lock.package_sha256,
                    "locked_by": (
                        getattr(lock.locked_by, "username", "") if lock.locked_by_id else ""
                    ),
                    "locked_at": lock.locked_at.isoformat() if lock.locked_at else "",
                }
                for lock in locks
            ],
        }
