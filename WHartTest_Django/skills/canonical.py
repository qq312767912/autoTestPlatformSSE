"""Skill 的「正本」归并（读侧视图）。

背景
----
Skill 历史上挂在项目下（``Skill.project``），同一个名字可能在不同项目里各存一条：
存量迁移（``0.0.0-migrated``）与 Skill 商店安装都会按项目各落一份。实测库里
34 条 Skill 只有 20 个不同名字，其中 7 个名字各在 3 个项目里各有一条。

Skill Hub 是**公共目录**——每个项目进来看到的应当是同一份内容，所以列表按 ``name``
归并成一条「正本」展示，否则 ``browser-use`` 会重复出现三次。

为什么这里只做读侧视图、不做数据层合并
----------------------------------------
真正的合并要改 ``SkillVersion.skill_id``、``TestCaseReview.selected_skill``、
``WorkflowSkillLock.skill_version`` 等的指向，还会撞上 ``(skill, version)`` 的版本号
冲突（例如 ``test-case-clarity-review`` 在项目 1 与项目 7 各有一个 ``1.0.0``），
属于不可逆操作，必须先在库上做备份与对账。这里保持零破坏：读侧归并 + 写侧落到正本，
池子随时间自然收敛。

正本选取优先级
--------------
``有活跃版本`` → ``版本数多`` → ``有人工确认的展示名`` → ``id 小``。

前两条保证「正在生产使用的那条」胜出，避免把带真实自进化历史的一份判成副本；
第三条只在前面都打平时生效：``display_name`` 是人在导入时确认过的结果，比「谁先建」
更有信息量——否则一个设过展示名的项目副本会被没有展示名的正本盖掉，列表上又变回
包内的英文标识符。``id 小`` 仍作兜底，让结果稳定可复现（同优先级下不随查询顺序漂移）。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from django.db.models import Count, QuerySet

from .models import Skill


def _rank(skill: Skill) -> tuple:
    """正本排序键：越大越优先。"""
    return (
        1 if skill.active_version_id else 0,
        getattr(skill, "n_versions", 0),
        # 打平时让"有人工确认展示名"的那条胜出：展示名是人的确认结果，
        # 丢掉它等于让列表退回包内英文标识符。放在版本数之后，不影响
        # "生产在用优先"这条主序。
        1 if getattr(skill, "display_name", "") else 0,
        # 兜底用 id 小者优先，取负号让 id 小的排前（元组整体取 max）。
        -skill.pk,
    )


def pick_canonical(skills: Sequence[Skill]) -> Dict[str, Skill]:
    """按 ``name`` 归并，返回 ``name -> 正本 Skill``。"""
    best: Dict[str, Skill] = {}
    for skill in skills:
        current = best.get(skill.name)
        if current is None or _rank(skill) > _rank(current):
            best[skill.name] = skill
    return best


def canonical_skills(queryset: QuerySet) -> Tuple[List[Skill], Dict[str, int]]:
    """把 ``queryset`` 归并成一份正本列表。

    返回 ``(正本列表, {name: 该名字在库里的副本数})``。

    副本数交给前端显式展示（「含 N 份副本」），而不是把多重性藏起来——这正是
    后续要不要做物理合并的判断依据。
    """
    rows = list(
        queryset.annotate(n_versions=Count("versions", distinct=True)).select_related(
            "active_version"
        )
    )
    copies: Dict[str, int] = {}
    for skill in rows:
        copies[skill.name] = copies.get(skill.name, 0) + 1

    canonical = pick_canonical(rows)
    # 按名字排序，保证列表顺序稳定（不依赖数据库默认排序）。
    ordered = sorted(canonical.values(), key=lambda skill: skill.name.lower())
    return ordered, copies
