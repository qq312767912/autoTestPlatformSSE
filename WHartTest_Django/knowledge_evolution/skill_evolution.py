"""T12：Skill 候选自进化服务。

职责边界（这一段决定了整个模块的安全性）：

**做**：从已人工确认的归因出发，在**旧版本的副本**上派生一个新候选包，
跑静态校验，输出可读 diff / 收益 / 影响范围 / 回滚目标。

**不做**（每一条都对应需求里的一句硬约束）：

- 不改 active 包。派生发生在 ``tempfile`` 临时目录，最后交给
  ``SkillVersionService.create_candidate_from_dir`` 落到**新的不可变版本目录**。
- 不从无证据反馈直接产包。入口第一件事就是校验归因全部为 ``confirmed``
  （``assert_confirmed_attributions``），且类别必须落在"Skill 可修"的范围内。
- 不因为候选生成失败而影响生产。整个流程对失败是隔离的：临时目录在 ``finally`` 清理，
  异常向上抛但库里的 active 版本一个字节都没动（``assert_active_untouched`` 兜底断言）。
- 不把知识/检索/环境问题塞进 Skill 包。这三类各有自己的候选通道
  （``knowledge_missing``/``knowledge_stale`` → 知识候选；``retrieval_error`` → 检索策略候选；
  ``environment_error`` → 必须修环境），混进来会产出"改不动根因"的包。

派生策略是**确定性**的：把已确认归因的假设转写成 ``SKILL.md`` 里一段受管护栏小节，
不依赖任何 LLM。理由是自进化链条里"生成候选"这一步如果本身不可复现，
后续的影子评测就无法判断"到底是候选变好了，还是这次生成随机性变好了"。
需要 LLM 参与时可以传入 ``patch`` 显式给出改动，改动本身照样被 diff 与审计记录。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from skills.validation import scan_package_dir
from skills.versions import SkillVersionService

from .attribution import assert_confirmed_attributions
from .knowledge_models import KnowledgeAuditLog
from .optimization import TYPE_BY_CATEGORY

#: 归因类型 → 是否属于"Skill 包可修复"。
#: 从 ``TYPE_BY_CATEGORY`` 派生而不是另抄一份，避免两张表慢慢漂移。
SKILL_ADDRESSABLE_TYPES = frozenset({"prompt", "skill_tool"})

#: Skill 包可修复的归因类别。
SKILL_ADDRESSABLE_CATEGORIES = frozenset(
    category for category, proposal_type in TYPE_BY_CATEGORY.items()
    if proposal_type in SKILL_ADDRESSABLE_TYPES
)

#: 不可由 Skill 包修复的类别 → 应走的通道。写出来是为了给出**可执行的**错误提示，
#: 而不是一句"不支持这个类别"。
ALTERNATIVE_CHANNELS = {
    "knowledge_missing": "应生成知识候选（knowledge）并走知识审核",
    "knowledge_stale": "应更新知识版本并走知识审核",
    "retrieval_error": "应生成检索策略候选（retrieval_policy）",
    "environment_error": "属于执行环境问题，需修复运行环境，改 Skill 包无效",
}

#: 受管护栏小节的起止标记。用固定标记而不是"追加到文件末尾"，
#: 是因为候选会反复派生：没有标记的话每次都在上次结果后面继续堆，包会越改越乱。
GUARDRAIL_BEGIN = "<!-- WHARTTEST-EVOLUTION-GUARDRAIL:BEGIN -->"
GUARDRAIL_END = "<!-- WHARTTEST-EVOLUTION-GUARDRAIL:END -->"

_GUARDRAIL_BLOCK = re.compile(
    re.escape(GUARDRAIL_BEGIN) + r".*?" + re.escape(GUARDRAIL_END),
    re.DOTALL,
)


#: SKILL.md 的 YAML frontmatter 整块（含首尾 ``---``）。分组 1 是内部内容。
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)


class SkillCandidateDeriver:
    """在包目录副本上构造候选内容。"""

    @classmethod
    def build_patch(cls, *, skill_version, attributions) -> dict:
        """由已确认归因构造确定性补丁。

        产出的是 ``{"edits": [...]}``，可以直接交给 ``apply_patch``，
        也可以被人读懂、被人改。这两种用途都要满足，所以不直接返回"新文件内容"。
        """
        items = list(attributions)
        skill_md = Path(skill_version.get_full_path() or "") / "SKILL.md"
        try:
            original = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            original = ""

        lines = [
            GUARDRAIL_BEGIN,
            "",
            "## 自进化护栏（平台自动维护）",
            "",
            "以下护栏由质量飞轮根据**已人工确认**的失败归因自动生成，",
            "对应归因记录：" + "、".join(sorted(str(item.id) for item in items)) + "。",
            "",
        ]
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. [{item.category}] {item.hypothesis.strip()}")
            for evidence in (item.evidence or [])[:3]:
                if isinstance(evidence, dict) and evidence.get("feedback_signals"):
                    lines.append(
                        f"   - 相关信号：{'、'.join(map(str, evidence['feedback_signals']))}"
                    )
            for counter in (item.counterevidence or [])[:2]:
                if isinstance(counter, dict) and counter.get("reason"):
                    # 反证一起写进包是刻意的：护栏若只写"要检查 X"，执行者会
                    # 把已恢复的场景也当成缺陷。把反证带上，执行时才知道何时不该报。
                    lines.append(f"   - 反证提示：{counter['reason']}")
        lines.extend(["", GUARDRAIL_END, ""])
        guardrail = "\n".join(lines)

        new_content = cls._replace_guardrail(original, guardrail)
        # 这里**不**推进版本号：补丁必须是纯粹由（基线 + 归因）决定的确定性产物，
        # 否则"同一批归因再派生一次"会因为版本号递增而产生一份"内容不同"的补丁，
        # 重复派生就再也判不出来了。版本推进属于机械动作，放在落盘前对工作副本执行。
        return {
            "strategy": "append_guardrail_section",
            "target": "SKILL.md",
            "edits": [{"path": "SKILL.md", "content": new_content}],
            "attribution_ids": sorted(str(item.id) for item in items),
            "categories": sorted({item.category for item in items}),
        }

    #: SKILL.md frontmatter 里的 version 行（只匹配 YAML 头部的顶层键）。
    _VERSION_LINE = re.compile(r"^(version\s*:\s*)(\S+)\s*$", re.MULTILINE)

    @classmethod
    def advance_package_version(cls, package_dir: Path, *, skill_version) -> str:
        """把**工作副本**包内 manifest 的版本号推进到下一个未占用号，返回新号。

        为什么必须换号：``SkillVersion`` 的唯一约束是 ``(skill, version)``，
        派生包内容与基线必然不同，若沿用基线的号会被"版本号已存在且内容不同"拒绝。

        为什么必须同时改包内 manifest 与库内版本号：两者不一致时会断掉"导出-重传"
        链路——导出该候选包，manifest 仍声明旧号，重新上传时与库里既有旧号撞车，
        永远传不回来。

        为什么写在**工作副本**上而不是直接写进补丁：补丁需要保持确定性，
        版本号却是每次调用都会变的机械产物。两者混在一起，重复派生就判不出来了。
        """
        from skills.versions import SkillVersionService

        skill_md = Path(package_dir) / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        next_version = SkillVersionService.next_version(skill_version.skill)
        match = _FRONTMATTER.match(content)
        if not match:
            # 没有 frontmatter 的包在静态校验阶段就会被拦下，这里不做任何改动，
            # 让后续校验去报"缺少 manifest"这个更准确的错。
            return ""
        inner = match.group(1)
        if cls._VERSION_LINE.search(inner):
            inner = cls._VERSION_LINE.sub(
                lambda item: f"{item.group(1)}{next_version}", inner, count=1,
            )
        else:
            inner = inner.rstrip("\n") + f"\nversion: {next_version}"
        # 只替换 frontmatter 内部区域，首尾 ``---`` 与正文原样保留。
        skill_md.write_text(
            content[:match.start(1)] + inner + content[match.end(1):], encoding="utf-8",
        )
        return next_version

    @staticmethod
    def _replace_guardrail(original: str, guardrail: str) -> str:
        """替换或追加受管护栏：已有则原地替换，保证候选可反复派生而不堆积。"""
        if _GUARDRAIL_BLOCK.search(original):
            return _GUARDRAIL_BLOCK.sub(guardrail.strip(), original, count=1)
        separator = "" if original.endswith("\n") or not original else "\n"
        return f"{original}{separator}\n{guardrail}"

    @staticmethod
    def apply_patch(*, source_dir: Path, patch: dict, work_dir: Path) -> Path:
        """把原包复制到 ``work_dir`` 并应用补丁，返回新包目录。

        先复制再改，是为了让"原包只读"成为结构性保证而不是纪律要求：
        即使补丁写错了路径，损坏的也只是副本。
        """
        source_dir = Path(source_dir)
        if not source_dir.is_dir():
            raise ValidationError("源版本包目录不存在，无法派生候选")
        work_dir = Path(work_dir)
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.copytree(source_dir, work_dir, symlinks=False)

        for edit in patch.get("edits") or []:
            relative = str(edit.get("path") or "").strip()
            if not relative:
                raise ValidationError("候选补丁存在缺少 path 的编辑项")
            target = (work_dir / relative).resolve()
            # 补丁来自归因链，理论上可信；但"理论上可信"不是路径安全的理由。
            if not str(target).startswith(str(work_dir.resolve())):
                raise ValidationError(f"候选补丁试图写到包目录之外：{relative}")
            if "content" in edit:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(edit["content"]), encoding="utf-8")
                continue
            find = edit.get("find")
            replace = edit.get("replace", "")
            if find is None:
                raise ValidationError(f"候选补丁项 {relative} 既没有 content 也没有 find")
            try:
                current = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ValidationError(f"候选补丁目标不可读：{relative}（{exc}）") from exc
            if find not in current:
                # 静默不替换会产出一个"看起来改过了、其实没改"的候选，
                # 后续影子评测会基于错误前提得出结论。
                raise ValidationError(f"候选补丁在 {relative} 中找不到待替换内容，已中止")
            target.write_text(current.replace(find, str(replace), 1), encoding="utf-8")

        for relative in patch.get("remove") or []:
            target = (work_dir / str(relative)).resolve()
            if not str(target).startswith(str(work_dir.resolve())):
                raise ValidationError(f"候选补丁试图删除包目录之外的文件：{relative}")
            if target.is_file():
                target.unlink()
        return work_dir


class SkillEvolutionService:
    """从一次真实失败派生 Skill 候选版本（T12 / R6）。"""

    @classmethod
    @transaction.atomic
    def derive_candidate(
        cls, *, skill_version, attributions, actor, patch: dict | None = None,
        change_reason: str = "", expected_benefit: str = "", impact_scope: str = "",
        api_key: str | None = None,
    ) -> dict:
        """派生候选包并落成新的 draft 版本，返回诊断信息。

        返回值刻意包含 ``diff`` / ``rollback_target`` / ``active_untouched`` 三项：
        需求 R6 要求候选产出时同时给出"可读 diff、变更原因、预期收益、影响范围和回滚目标"，
        把这几项放在同一个返回值里，调用方就无法只取一半。
        """
        items = list(attributions)
        if not items:
            raise ValidationError("至少需要一条已确认归因才能派生候选")
        if skill_version is None:
            raise ValidationError("缺少基线 Skill 版本，无法派生候选")

        # 硬校验 1：归因必须全部经人工确认。
        assert_confirmed_attributions(items)

        # 硬校验 2：类别必须落在 Skill 包可修复的范围内，否则给出该走哪条通道。
        unsupported = sorted({
            item.category for item in items
            if item.category not in SKILL_ADDRESSABLE_CATEGORIES
        })
        if unsupported:
            hints = "；".join(
                f"{category}：{ALTERNATIVE_CHANNELS.get(category, '不属于 Skill 包可修复范围')}"
                for category in unsupported
            )
            raise ValidationError(f"以下归因不能通过修改 Skill 包修复——{hints}")

        # 硬校验 3：基线必须仍是当前活跃版本，否则派生目标就是错的。
        skill = skill_version.skill
        if skill.active_version_id and str(skill.active_version_id) != str(skill_version.id):
            raise ValidationError(
                f"派生基线必须是当前活跃版本；{skill_version.version} 已不是活跃版本，"
                f"请基于 {skill.active_version.version} 重新派生"
            )

        baseline_sha = skill_version.package_sha256
        resolved_patch = patch or SkillCandidateDeriver.build_patch(
            skill_version=skill_version, attributions=items,
        )

        # 硬校验 4：同一批归因（对同一条基线）只能派生一次。
        #
        # 这一步必须靠**补丁指纹**判，不能靠"新包哈希是否等于旧包哈希"：候选落库时
        # 版本号一定会被推进（见 ``advance_package_version``），因此重复派生产出的
        # 包哈希天然不同，事后比对哈希永远判不出重复。指纹取自确定性补丁本身，
        # 与版本号无关，才能真实反映"改动是否同一批"。
        fingerprint = patch_fingerprint(resolved_patch)
        existing = SkillVersionService.find_derived_candidate(
            skill_version.skill, derivation_fingerprint=fingerprint,
        )
        if existing is not None:
            raise ValidationError(
                f"同一批归因已派生过候选版本 {existing.version}，请勿重复派生；"
                "如需再次派生，请补充新的已确认归因或显式提供 patch"
            )

        source_dir = Path(skill_version.get_full_path() or "")
        work_dir = Path(tempfile.mkdtemp(prefix=f"skill-evolve-{skill_version.skill_id}-"))
        try:
            working = work_dir / "package"
            SkillCandidateDeriver.apply_patch(
                source_dir=source_dir, patch=resolved_patch, work_dir=working,
            )
            # 版本推进只作用于工作副本：补丁保持确定性，版本号在这里才落。
            applied_version = SkillCandidateDeriver.advance_package_version(
                working, skill_version=skill_version,
            )

            # 静态校验在**落盘之前**做：不合格的候选不该在库里留下任何痕迹。
            report = scan_package_dir(working)
            if not report.ok:
                detail = "；".join(issue.message for issue in report.errors[:5])
                raise ValidationError(f"候选包静态校验未通过：{detail}")

            reason = change_reason or (
                f"依据 {len(items)} 条已确认归因派生候选（"
                + "、".join(sorted({item.category for item in items})) + "）"
            )
            _, candidate = SkillVersionService.create_candidate_from_dir(
                source_dir=working, project=skill.project, actor=actor,
                source_type="evolution",
                source_metadata={
                    "parent_version_id": str(skill_version.id),
                    "parent_version": skill_version.version,
                    "parent_package_sha256": baseline_sha,
                    "attribution_ids": resolved_patch.get("attribution_ids", []),
                    "categories": resolved_patch.get("categories", []),
                    "patch_strategy": resolved_patch.get("strategy", ""),
                    "derivation": "deterministic-guardrail",
                    "derivation_fingerprint": fingerprint,
                    "applied_version": applied_version,
                },
                change_reason=reason,
                expected_benefit=expected_benefit or (
                    "减少 " + "、".join(sorted({item.category for item in items})) + " 类失败"
                ),
                impact_scope=impact_scope or f"仅影响 {skill.name} 的候选版本，不影响活跃版本",
                api_key=api_key,
            )

            # 兜底断言 1：原包必须一个字节都没变。
            integrity = SkillVersionService.verify_package_integrity(skill_version)
            if not integrity["ok"]:
                raise ValidationError(
                    "派生过程触碰了基线包，已中止（基线哈希发生变化，违反不可变约束）"
                )

            # 兜底断言 2：必须产生**实质变更**。
            # 正常情况下前面按指纹拦下的重复派生已经覆盖了这条路；这里保留是因为
            # 调用方可以显式传 patch，而显式 patch 不一定带指纹语义。若产出的候选
            # 命中基线版本本身，说明这次派生什么都没改——此时若不拦，调用方会以为
            # 拿到了新候选去走评测与审批，实际上审的还是活跃版本，一次自欺欺人的发布演练。
            if str(candidate.id) == str(skill_version.id):
                raise ValidationError(
                    "派生结果与基线版本内容完全相同，未产生任何实质变更；"
                    "请补充新的已确认归因或显式提供 patch"
                )

            diff = SkillVersionService.diff(skill_version, candidate)
            KnowledgeAuditLog.record(
                project_id=skill.project_id, actor=actor, action="create", entity=candidate,
                from_state="", to_state="draft", reason=reason,
                detail={
                    "source": "skill_evolution",
                    "parent_version_id": str(skill_version.id),
                    "candidate_version_id": str(candidate.id),
                    "candidate_package_sha256": candidate.package_sha256,
                    "categories": resolved_patch.get("categories", []),
                    "diff_summary": diff.get("summary", ""),
                    "active_untouched": True,
                },
            )
            return {
                "skill": skill,
                "candidate": candidate,
                "diff": diff,
                "patch": resolved_patch,
                "change_reason": reason,
                "validation_report": report.as_dict(),
                "rollback_target": str(skill_version.id),
                "active_untouched": True,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ------------------------------------------------------------ 后续链路

    @classmethod
    def evaluate_candidate(
        cls, *, candidate, baseline_run, candidate_run, actor=None,
        thresholds: dict | None = None, required_partitions: tuple | None = None,
    ):
        """对候选跑硬门禁（T09）并落不可变快照。

        这一层刻意**只跑门禁、不改状态**：门禁通过不等于可以激活，
        中间还必须有负责人审批（T13）。把两件事分开，才能保证
        "门禁通过但审批被驳回"这种正常情况不会误激活。
        """
        from .evaluation_gates import EvaluationGateService

        return EvaluationGateService.run_gate(
            candidate_run=candidate_run, baseline_run=baseline_run,
            release=candidate.release, skill_version=candidate,
            thresholds=thresholds, actor=actor, required_partitions=required_partitions,
        )


def assert_derivation_allowed(skill_version, attributions) -> list:
    """只做检查、不派生的轻量入口，供前端在按钮上做前置提示。

    与 ``derive_candidate`` 共用同一套判据，避免"前端说可以、后端报错"。
    """
    problems: list[dict] = []
    items = list(attributions)
    if not items:
        return [{"code": "no_attribution", "label": "归因", "detail": "未选择任何归因"}]
    unconfirmed = [item for item in items if item.state != "confirmed"]
    if unconfirmed:
        problems.append({
            "code": "attribution_unconfirmed", "label": "归因确认",
            "detail": f"{len(unconfirmed)} 条归因尚未人工确认，不能派生候选",
        })
    for category in sorted({item.category for item in items}):
        if category not in SKILL_ADDRESSABLE_CATEGORIES:
            problems.append({
                "code": f"category_{category}", "label": "归因类别",
                "detail": f"{category} 不能通过修改 Skill 包修复——"
                          f"{ALTERNATIVE_CHANNELS.get(category, '请改用其它候选通道')}",
            })
    if skill_version is None:
        problems.append({"code": "baseline_missing", "label": "基线版本", "detail": "缺少基线 Skill 版本"})
    elif skill_version.skill.active_version_id and str(skill_version.skill.active_version_id) != str(skill_version.id):
        problems.append({
            "code": "baseline_not_active", "label": "基线版本",
            "detail": "派生基线必须是当前活跃版本",
        })

    # 重复派生提示：只有在前面各项都过了才有意义——基线不对时算出来的指纹没参考价值。
    if not problems:
        try:
            fingerprint = patch_fingerprint(
                SkillCandidateDeriver.build_patch(
                    skill_version=skill_version, attributions=items,
                )
            )
        except (OSError, UnicodeDecodeError, ValueError):
            fingerprint = ""
        existing = SkillVersionService.find_derived_candidate(
            skill_version.skill, derivation_fingerprint=fingerprint,
        )
        if existing is not None:
            problems.append({
                "code": "duplicate_derivation", "label": "重复派生",
                "detail": f"同一批归因已派生过候选版本 {existing.version}，请勿重复派生",
            })
    return problems


def patch_fingerprint(patch: dict) -> str:
    """补丁指纹：同一组改动重复派生时可用于幂等判断。"""
    raw = json.dumps(patch, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
