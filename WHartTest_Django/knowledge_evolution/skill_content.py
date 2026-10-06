"""T13：`skill_content` 候选、评测与上线闭环。

职责边界（这一段决定了整个模块的安全性）：

**做**：把工坊里**已经人工确认**的归因，转成一份**最小增量**的 Skill 包内容补丁，
落成**新的不可变 SkillVersion**，再在**同一份冻结金标**上做基线/候选对照，
跑完 T09 门禁 + T13 四条硬门禁后交给负责人审批、灰度、激活、观察、回滚。

**不做**（每条都对应需求里的一句硬约束）：

- **不改 active 包**。派生发生在临时目录，落盘走
  ``SkillVersionService.create_candidate_from_dir``，最后用
  ``verify_package_integrity`` 断言基线包一个字节都没动。
- **不从无证据的归因出发**。入口第一件事是 ``assert_confirmed_attributions``。
- **不把环境问题写进补丁**。``assert_usable_for_content_patch`` 会把 environment
  层的归因剔除；剔完为空就拒绝，并说明"改 Skill 无效，先修环境"。
- **不放过白名单之外的文件**。``assert_patch_within_whitelist`` 校验写路径与删除
  路径，绝对路径 / ``..`` 越界 / 任意路径一律拒绝。
- **不用"均分涨了"当晋级依据**。T13 的四条硬门禁是逐样本的：本轮 Badcase 必须
  真的修好、关键回归必须零退化、包契约必须通过、隐藏集不许被当成优化目标。
  均分涨而关键样本退化，恰恰是要拦住的典型情形。

门禁与状态流转**分开**：本模块只判定并落证据（``OptimizationExperiment``），
状态流转仍统一在 ``capabilities.CapabilityReleaseService``。
"""
from __future__ import annotations

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction

from .attribution import assert_confirmed_attributions, assert_usable_for_content_patch
from .evaluation import CaseComparisonService
from .knowledge_models import KnowledgeAuditLog
from .optimization import SKILL_CONTENT_CATEGORIES, SKILL_CONTENT_TYPE
from .optimization_models import OptimizationExperiment
from .skill_evolution import (
    ALTERNATIVE_CHANNELS,
    SkillContentPatchBuilder,
    SkillEvolutionService,
)

#: 目标 Badcase 判定"已修复"的分数下限。
DEFAULT_BADCASE_FLOOR = 0.80

#: 关键回归案例判定"基线本来是通过的"的分数下限。
DEFAULT_KEY_CASE_FLOOR = 0.80

#: 认为某条回归样本"关键"的元数据键。显式标注优先；一条都没标时退化为
#: "基线已通过的回归样本"——否则"没人标"就等于没门禁，形同虚设。
KEY_CASE_FLAGS = ("key_regression", "key", "is_key")

#: 冻结比对用的配置键。其余键（role / skill_version_id / package_sha256）是
#: "这一侧是谁"的身份信息，比了必然不等，不是"冻结条件是否一致"的问题。
FREEZE_CONFIG_KEYS = (
    "model", "model_name", "llm", "llm_config", "temperature",
    "prompt", "prompt_version", "top_k", "seed",
)

#: T13 硬门禁编码 → 中文说明。前端"缺失条件"直接读这份映射。
SKILL_CONTENT_GATE_LABELS = {
    "badcase_fixed": "本轮 Badcase 未修复",
    "key_regression_zero_degradation": "关键回归案例退化",
    "schema_passed": "包契约未通过校验",
    "hidden_isolation": "隐藏集被当作优化目标",
    "freeze_comparable": "基线/候选冻结条件不一致",
}

#: 隐藏集隔离：这些分区不得作为优化目标。
HIDDEN_SPLITS = ("hidden",)


def _fingerprint(payload) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SkillContentOptimizationService:
    """把 `skill_content` 优化建议落成新的不可变 SkillVersion。"""

    # ------------------------------------------------------------------ 基线

    @staticmethod
    def _version_of_release(release):
        from skills.models import SkillVersion

        if release is None:
            return None
        return SkillVersion.objects.filter(release=release).select_related("skill").first()

    @classmethod
    def resolve_baseline_skill_version(cls, proposal):
        """定位派生基线，优先级从高到低：

        1. 提案 ``change_patch.baseline_skill_version_id``（工坊页显式选定）；
        2. 能力定义的 ``active_release``（"我正在进化的这条能力当前生效的版本"）；
        3. 提案的 ``baseline_release``（评测基线）。

        三级回退而不是"必须显式指定"，是因为工坊里绝大多数时候想改的就是
        "当前生效的那一版"，逼用户再选一次只会带来选错的风险。
        """
        from skills.models import SkillVersion

        requested = str(
            (proposal.change_patch or {}).get("baseline_skill_version_id") or ""
        ).strip()
        if requested:
            version = SkillVersion.objects.filter(pk=requested).select_related("skill").first()
            if version is None:
                raise ValidationError("指定的基线 Skill 版本不存在")
            return version

        definition = proposal.capability
        if definition is not None and definition.active_release_id:
            version = cls._version_of_release(definition.active_release)
            if version is not None:
                return version

        version = cls._version_of_release(proposal.baseline_release)
        if version is not None:
            return version

        raise ValidationError(
            "缺少基线 Skill 版本，无法生成 Skill 内容候选："
            "请在候选的 change_patch 里指定 baseline_skill_version_id，"
            "或先把优化建议绑定到带活跃发布的能力定义上"
        )

    # ------------------------------------------------------------ 派生候选

    @classmethod
    def _existing_candidate(cls, proposal):
        from skills.models import SkillVersion

        existing_id = str(
            (proposal.change_patch or {}).get("materialized_skill_version_id") or ""
        ).strip()
        if not existing_id:
            return None
        return (
            SkillVersion.objects.filter(pk=existing_id)
            .select_related("skill", "release")
            .first()
        )

    @classmethod
    @transaction.atomic
    def materialize(cls, *, proposal, actor=None, edits=None, api_key=None) -> dict:
        """派生候选包；幂等（同一提案重复调用返回同一候选）。

        返回字典刻意同时带出 ``diff`` / ``rollback_target`` / ``active_untouched``：
        需求 R12 要求候选产出时一并给出"变更原因、预期收益、影响范围和回滚目标"，
        放在同一个返回值里，调用方就无法只取一半。
        """
        if proposal.proposal_type != SKILL_CONTENT_TYPE:
            raise ValidationError("该服务只处理 skill_content 候选项")
        if proposal.state != "draft":
            raise ValidationError("只有草稿优化建议可以生成候选")

        baseline = cls.resolve_baseline_skill_version(proposal)
        if baseline.skill.project_id != proposal.project_id:
            raise ValidationError("基线 Skill 版本与优化候选不属于同一项目")

        reused = cls._existing_candidate(proposal)
        if reused is not None:
            return {
                "proposal": proposal,
                "release": reused.release,
                "candidate": reused,
                "baseline": baseline,
                "patch": {},
                "diff": {},
                "rollback_target": str(baseline.id),
                "active_untouched": True,
                "reused": True,
            }

        attributions = list(proposal.attributions.all())
        if not attributions:
            raise ValidationError("候选必须来自已确认归因，当前没有任何归因")

        # 硬校验 1：全部经人工确认。
        assert_confirmed_attributions(attributions)

        # 硬校验 2：环境层归因剔除（不是报错——同一次运行里经常是"环境故障 + 真内容问题"并存）。
        usable = assert_usable_for_content_patch(attributions)
        if not usable:
            raise ValidationError(
                "所选归因全部属于执行环境问题，改 Skill 包内容无效，请先修复运行环境"
            )

        # 硬校验 3：类别必须落在 Skill 包可修复的范围内，并给出该走哪条通道。
        unsupported = sorted({
            item.category for item in usable
            if item.category not in SKILL_CONTENT_CATEGORIES
        })
        if unsupported:
            hints = "；".join(
                f"{category}：{ALTERNATIVE_CHANNELS.get(category, '不属于 Skill 包可修复范围')}"
                for category in unsupported
            )
            raise ValidationError(f"以下归因不能通过修改 Skill 包内容修复——{hints}")

        # 硬校验 4：禁止用于优化的金标样本不得进入候选。
        prohibited = proposal.attributions.filter(
            output__gold_cases__allow_optimization=False,
        ).exists()
        if prohibited:
            raise ValidationError("候选包含禁止用于优化的金标样本")

        categories = sorted({item.category for item in usable})
        patch = SkillContentPatchBuilder.build_patch(
            skill_version=baseline, attributions=usable, edits=edits,
        )

        result = SkillEvolutionService.derive_candidate(
            skill_version=baseline,
            attributions=usable,
            actor=actor,
            patch=patch,
            change_reason=(
                f"工坊依据 {len(usable)} 条已确认归因生成 Skill 内容候选"
                f"（{'、'.join(categories)}）"
            ),
            expected_benefit=f"减少 {'、'.join(categories)} 类失败",
            impact_scope=f"仅新增 {baseline.skill.name} 的候选版本，不改动 active 包",
            api_key=api_key,
        )
        candidate = result["candidate"]
        release = candidate.release
        usable_ids = {item.pk for item in usable}
        excluded = sorted(
            str(item.id) for item in attributions if item.pk not in usable_ids
        )

        proposal.change_patch = {
            **(proposal.change_patch or {}),
            "materialized_release_id": str(release.id),
            "materialized_skill_version_id": str(candidate.id),
            "baseline_skill_version_id": str(baseline.id),
            "baseline_package_sha256": baseline.package_sha256,
            "patch_strategy": patch.get("strategy", ""),
            "changed_paths": patch.get("whitelist_paths", []),
            "diff_summary": (result.get("diff") or {}).get("summary", ""),
            "excluded_attribution_ids": excluded,
        }
        proposal.risk_notes = [
            *(proposal.risk_notes or []),
            "候选尚未做冻结集对照：Badcase 是否修复、关键回归是否零退化均为未知",
            "候选必须经负责人审批后才能激活，激活后必须经过观察窗口",
        ]
        proposal.rollback_plan = {
            "strategy": "rollback_skill_release",
            "baseline_skill_version_id": str(baseline.id),
            "baseline_release_id": str(baseline.release_id or ""),
            "baseline_version": baseline.version,
        }
        proposal.save(update_fields=["change_patch", "risk_notes", "rollback_plan", "updated_at"])

        KnowledgeAuditLog.record(
            project_id=proposal.project_id, actor=actor, action="create",
            entity=candidate, from_state="", to_state="draft",
            reason="工坊派发 Skill 内容候选",
            detail={
                "source": "skill_content_optimization",
                "proposal_id": str(proposal.id),
                "baseline_version_id": str(baseline.id),
                "baseline_package_sha256": baseline.package_sha256,
                "candidate_version_id": str(candidate.id),
                "candidate_package_sha256": candidate.package_sha256,
                "changed_paths": patch.get("whitelist_paths", []),
                "excluded_environment_attributions": excluded,
                "active_untouched": result["active_untouched"],
            },
        )
        return {
            "proposal": proposal,
            "release": release,
            "candidate": candidate,
            "baseline": baseline,
            "patch": patch,
            "diff": result.get("diff") or {},
            "rollback_target": result["rollback_target"],
            "active_untouched": result["active_untouched"],
            "reused": False,
        }


class SkillContentGateService:
    """T13 硬门禁：在 T09 通用门禁之上，补四条**逐样本**的判定。"""

    # ------------------------------------------------------------------ 输入

    @staticmethod
    def target_case_ids(proposal) -> list:
        """本轮 Badcase 对应的评测样本。

        链路是 ``FailureAttribution.output`` → ``EvaluationCase.source_output``：
        归因是针对某一份产出提的，而那份产出若被收进金标就成了某条评测样本。
        取不到就说明"这次失败还没有对应样本"，后续门禁会显式报出来，而不是放过。
        """
        from .models import EvaluationCase

        output_ids = {
            value for value in proposal.attributions.values_list("output_id", flat=True)
            if value
        }
        if not output_ids:
            return []
        return sorted(
            str(value) for value in EvaluationCase.objects.filter(
                source_output_id__in=output_ids,
            ).values_list("id", flat=True)
        )

    @staticmethod
    def _cases_by_id(case_ids):
        from .models import EvaluationCase

        return {
            str(case.id): case
            for case in EvaluationCase.objects.filter(id__in=case_ids)
        }

    # ---------------------------------------------------------------- 判定

    @classmethod
    def badcase_report(cls, *, proposal, baseline_run, candidate_run,
                       floor: float = DEFAULT_BADCASE_FLOOR) -> dict:
        """本轮 Badcase 是否修好：目标样本逐条达到下限且不劣于基线。"""
        case_ids = cls.target_case_ids(proposal)
        if not case_ids:
            return {
                "ok": False,
                "case_ids": [],
                "cases": [],
                "detail": (
                    "本轮归因对应的产出还没有进入冻结评测集，"
                    "无法证明 Badcase 已修复（补齐金标样本后重跑）"
                ),
            }
        baseline = CaseComparisonService.score_map(baseline_run)
        candidate = CaseComparisonService.score_map(candidate_run)
        cases = []
        for case_id in case_ids:
            before = baseline.get(case_id)
            after = candidate.get(case_id)
            cases.append({
                "case_id": case_id,
                "baseline": before,
                "candidate": after,
                "fixed": (
                    after is not None
                    and after >= floor
                    and (before is None or after >= before)
                ),
            })
        broken = [row for row in cases if not row["fixed"]]
        return {
            "ok": not broken,
            "floor": floor,
            "case_ids": case_ids,
            "cases": cases,
            "detail": (
                "本轮 Badcase 全部修复"
                if not broken else
                f"{len(broken)} 条目标样本未修复（未达下限 {floor} 或劣于基线）"
            ),
        }

    @classmethod
    def key_regression_report(cls, *, baseline_run, candidate_run,
                              split: str = "regression",
                              floor: float = DEFAULT_KEY_CASE_FLOOR) -> dict:
        """关键回归案例零退化。

        "关键"的判定顺序：样本 ``metadata`` 显式标注（``key_regression`` /
        ``key`` / ``is_key``）优先；一条都没标时退化为"基线已经通过的回归样本"
        （分数 ≥ ``floor``）。两条都不满足就是没有关键样本，此时门禁**不通过**
        ——"没有可判定的回归基线"和"回归没退化"是两件事。
        """
        baseline = CaseComparisonService.score_map(baseline_run, split=split)
        candidate = CaseComparisonService.score_map(candidate_run, split=split)
        cases = cls._cases_by_id(set(baseline) | set(candidate))
        explicitly_key = {
            case_id for case_id, case in cases.items()
            if any((case.metadata or {}).get(flag) for flag in KEY_CASE_FLAGS)
        }
        if explicitly_key:
            key_ids = sorted(explicitly_key)
            source = "annotated"
        else:
            key_ids = sorted(
                case_id for case_id, score in baseline.items() if score >= floor
            )
            source = "baseline_passed"
        if not key_ids:
            return {
                "ok": False,
                "source": source,
                "split": split,
                "floor": floor,
                "key_case_ids": [],
                "degraded": [],
                "detail": "没有可判定的关键回归样本，无法证明零退化",
            }
        degraded = []
        for case_id in key_ids:
            before = baseline.get(case_id)
            after = candidate.get(case_id)
            if before is None or after is None or after < before:
                degraded.append({
                    "case_id": case_id,
                    "baseline": before,
                    "candidate": after,
                    "delta": None if before is None or after is None
                    else round(after - before, 6),
                })
        return {
            "ok": not degraded,
            "source": source,
            "split": split,
            "floor": floor,
            "key_case_ids": key_ids,
            "degraded": degraded,
            "detail": (
                f"{len(key_ids)} 条关键回归样本零退化"
                if not degraded else f"{len(degraded)} 条关键回归样本退化"
            ),
        }

    @staticmethod
    def schema_report(candidate_version) -> dict:
        """包契约必须通过：静态校验无 error，且包哈希与库内记录一致。"""
        from skills.versions import SkillVersionService

        report = dict(candidate_version.validation_report or {})
        errors = report.get("errors") or []
        integrity = SkillVersionService.verify_package_integrity(candidate_version)
        ok = bool(report.get("ok")) and not errors and bool(integrity.get("ok"))
        problems = []
        if not report.get("ok"):
            problems.append("静态校验存在 error：" + "；".join(
                str(item.get("message") or item.get("code") or item) for item in errors[:5]
            ))
        if not integrity.get("ok"):
            problems.append(f"包完整性问题：{integrity.get('reason') or '哈希不一致'}")
        return {
            "ok": ok,
            "package_sha256": candidate_version.package_sha256,
            "files": len(report.get("files") or []),
            "errors": errors,
            "integrity": integrity,
            "detail": "包契约校验通过" if ok else "；".join(problems),
        }

    @classmethod
    def hidden_isolation_report(cls, *, proposal, candidate_run) -> dict:
        """隐藏集隔离：优化目标样本不得出现在隐藏分区里。

        为什么这条必须是硬门禁：隐藏集的意义就是"谁都没见过"。一旦拿隐藏样本
        当优化目标（哪怕只是间接：目标 Badcase 恰好落在 hidden 分区），
        这批样本的分数就不再是"泛化能力"，后面的通率与门禁结论全部失真。
        """
        from .models import EvaluationCase

        target_ids = set(cls.target_case_ids(proposal))
        hidden_ids = set(
            str(value) for value in EvaluationCase.objects.filter(
                split__in=HIDDEN_SPLITS,
            ).values_list("id", flat=True)
        )
        leaked = sorted(target_ids & hidden_ids)
        # 第二道：同一份产出既被当优化目标、又被收进隐藏集，等于换了个 case 记录
        # 绕开第一道判据，所以按 source_output 再查一次。
        shared_outputs = []
        if target_ids:
            hidden_outputs = set(
                str(value) for value in EvaluationCase.objects.filter(
                    split__in=HIDDEN_SPLITS, source_output__isnull=False,
                ).values_list("source_output_id", flat=True)
            )
            target_outputs = set(
                str(value) for value in proposal.attributions.values_list("output_id", flat=True)
                if value
            )
            shared_outputs = sorted(target_outputs & hidden_outputs)
        ok = not leaked and not shared_outputs
        return {
            "ok": ok,
            "hidden_splits": list(HIDDEN_SPLITS),
            "target_case_count": len(target_ids),
            "leaked_case_ids": leaked,
            "leaked_output_ids": shared_outputs,
            "detail": (
                "隐藏集与优化目标无交集"
                if ok else "隐藏集样本被当作优化目标，评测结论不可用于晋级"
            ),
        }

    @staticmethod
    def freeze_report(*, baseline_run, candidate_run, gold_version) -> dict:
        """基线/候选必须跑在**同一份冻结金标 + 同一模型配置**上（R13）。

        基线与候选各跑一次、配置不同，指标就没有可比性；此时"候选更好"可能只是
        这次温度更低。所以先比冻结快照哈希，再比与模型相关的配置键。
        """
        problems = []
        if gold_version is None:
            problems.append("缺少冻结金标版本")
        else:
            if gold_version.state != "frozen" or not gold_version.content_hash:
                problems.append("金标版本尚未冻结，回放结果不可作为晋级依据")
            for run, label in ((baseline_run, "基线"), (candidate_run, "候选")):
                if run is not None and gold_version.content_hash and \
                        run.replay_hash != gold_version.content_hash:
                    problems.append(f"{label}评测不是在这份冻结金标上回放的")
        left = {key: (baseline_run.config or {}).get(key) for key in FREEZE_CONFIG_KEYS
                if key in (baseline_run.config or {})} if baseline_run else {}
        right = {key: (candidate_run.config or {}).get(key) for key in FREEZE_CONFIG_KEYS
                 if key in (candidate_run.config or {})} if candidate_run else {}
        mismatched = sorted(
            key for key in set(left) | set(right) if left.get(key) != right.get(key)
        )
        if mismatched:
            problems.append("基线/候选冻结配置不一致：" + "、".join(mismatched))
        return {
            "ok": not problems,
            "gold_dataset_version_id": str(getattr(gold_version, "id", "") or ""),
            "replay_hash": getattr(gold_version, "content_hash", "") or "",
            "compared_keys": sorted(set(left) & set(right)),
            "mismatched_keys": mismatched,
            "detail": "基线/候选冻结条件一致" if not problems else "；".join(problems),
        }

    # ---------------------------------------------------------------- 入口

    @classmethod
    @transaction.atomic
    def run(cls, *, proposal, gold_version, baseline_run, candidate_run, actor=None,
            thresholds=None, kind: str = "full"):
        """跑完 T09 通用门禁 + T13 四条硬门禁，落一份 ``OptimizationExperiment``。"""
        from .evaluation_gates import EvaluationGateService

        candidate_version = cls._candidate_version(proposal)
        if candidate_version is None:
            raise ValidationError("候选尚未派生 Skill 版本，无法执行门禁")

        snapshot = EvaluationGateService.run_gate(
            candidate_run=candidate_run, baseline_run=baseline_run,
            release=candidate_version.release, skill_version=candidate_version,
            thresholds=thresholds, actor=actor, kind=kind,
        )

        checks: list[dict] = [{
            "code": "general_gate", "label": "通用评测门禁",
            "ok": bool(snapshot.passed),
            "detail": "通用门禁通过" if snapshot.passed else "通用门禁未通过",
            "snapshot_id": str(snapshot.id),
            "content_hash": snapshot.content_hash,
        }]
        details = {
            "badcase": cls.badcase_report(
                proposal=proposal, baseline_run=baseline_run, candidate_run=candidate_run,
            ),
            "key_regression": cls.key_regression_report(
                baseline_run=baseline_run, candidate_run=candidate_run,
            ),
            "schema": cls.schema_report(candidate_version),
            "hidden_isolation": cls.hidden_isolation_report(
                proposal=proposal, candidate_run=candidate_run,
            ),
            "freeze": cls.freeze_report(
                baseline_run=baseline_run, candidate_run=candidate_run,
                gold_version=gold_version,
            ),
        }
        for code, report in details.items():
            checks.append({
                "code": code, "label": SKILL_CONTENT_GATE_LABELS.get(code, code),
                "ok": bool(report.get("ok")), "detail": report.get("detail", ""),
            })
        passed = all(item["ok"] for item in checks)
        release = candidate_version.release
        release_report = {
            "passed": passed,
            "kind": "skill_content",
            "snapshot_id": str(snapshot.id),
            "snapshot_content_hash": snapshot.content_hash,
            "checks": checks,
        }
        if release is not None:
            from .capabilities import assert_can_reach

            release.baseline_run = baseline_run
            release.candidate_run = candidate_run
            release.gate_report = release_report
            update_fields = [
                "baseline_run", "candidate_run", "gate_report", "updated_at",
            ]
            if passed:
                # 走到这里就意味着"冻结集影子评测"已经跑完并通过，版本进入影子状态：
                # ``draft`` 不能直接跳 ``awaiting_approval``（状态图里没有这条边），
                # 而"跑完影子评测"本来就该把状态推到 shadow——不是为了让审批通过
                # 而硬塞一个状态，而是这一步的语义就是影子评测。
                assert_can_reach(release.state, "shadow")
                release.state = "shadow"
                update_fields.append("state")
            release.save(update_fields=update_fields)

        experiment = OptimizationExperiment.objects.create(
            proposal=proposal, gold_dataset_version=gold_version,
            baseline_run=baseline_run, candidate_run=candidate_run,
            candidate_release=release,
            status="passed" if passed else "failed",
            gate_report={
                "kind": "skill_content",
                "passed": passed,
                "snapshot_id": str(snapshot.id),
                "snapshot_content_hash": snapshot.content_hash,
                "checks": checks,
                "details": details,
            },
            shadow_metrics={
                "baseline": snapshot.metrics.get("baseline", {}),
                "candidate": snapshot.metrics.get("candidate", {}),
                "quality_regression": snapshot.metrics.get("quality_regression"),
            },
            created_by=actor,
        )
        proposal.state = "awaiting_approval" if passed else "draft"
        proposal.save(update_fields=["state", "updated_at"])
        KnowledgeAuditLog.record(
            project_id=proposal.project_id, actor=actor,
            action="evaluate", entity=experiment,
            from_state="draft", to_state=experiment.status,
            reason="Skill 内容候选冻结集对照与硬门禁",
            detail={
                "source": "skill_content_gate",
                "proposal_id": str(proposal.id),
                "passed": passed,
                "failed_checks": [item["code"] for item in checks if not item["ok"]],
                "snapshot_id": str(snapshot.id),
            },
        )
        return experiment

    # ------------------------------------------------------------------ 查询

    @staticmethod
    def _candidate_version(proposal):
        from skills.models import SkillVersion

        version_id = str(
            (proposal.change_patch or {}).get("materialized_skill_version_id") or ""
        ).strip()
        if not version_id:
            return None
        return (
            SkillVersion.objects.filter(pk=version_id)
            .select_related("skill", "release")
            .first()
        )

    @classmethod
    def latest_experiment(cls, proposal) -> OptimizationExperiment | None:
        return (
            OptimizationExperiment.objects.filter(proposal=proposal)
            .select_related("baseline_run", "candidate_run", "candidate_release")
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def summary(cls, proposal) -> dict:
        """给工坊页面用的门禁摘要（含缺失条件），无实验时返回空摘要而不是报错。"""
        from skills.models import SkillVersion

        candidate = cls._candidate_version(proposal)
        baseline_id = str(
            (proposal.change_patch or {}).get("baseline_skill_version_id") or ""
        ).strip()
        baseline = (
            SkillVersion.objects.filter(pk=baseline_id).select_related("skill").first()
            if baseline_id else None
        )
        experiment = cls.latest_experiment(proposal)
        report = dict(experiment.gate_report or {}) if experiment else {}
        checks = report.get("checks") or []
        return {
            "proposal_id": str(proposal.id),
            "proposal_state": proposal.state,
            "proposal_type": proposal.proposal_type,
            "baseline_skill_version_id": str(getattr(baseline, "id", "") or ""),
            "baseline_version": getattr(baseline, "version", "") or "",
            "candidate_skill_version_id": str(getattr(candidate, "id", "") or ""),
            "candidate_version": getattr(candidate, "version", "") or "",
            "candidate_release_id": str(getattr(candidate, "release_id", "") or ""),
            "candidate_release_state": getattr(getattr(candidate, "release", None), "state", ""),
            "changed_paths": (proposal.change_patch or {}).get("changed_paths", []),
            "patch_strategy": (proposal.change_patch or {}).get("patch_strategy", ""),
            "rollback_plan": proposal.rollback_plan or {},
            "risk_notes": proposal.risk_notes or [],
            "experiment_id": str(getattr(experiment, "id", "") or ""),
            "gate_passed": bool(report.get("passed")),
            "gate_checks": checks,
            "gate_details": report.get("details") or {},
            "missing_conditions": [
                {"code": item["code"], "detail": item.get("detail", "")}
                for item in checks if not item.get("ok")
            ],
        }


class SkillContentLifecycleService:
    """审批 / 灰度 / 激活 / 观察 / 回滚（R13）。

    全部委托 ``CapabilityReleaseService``：那是唯一的发布状态机真值。
    这里只加两道 T13 专属的**前置条件**——门禁没通过不许提交审批、
    审批前必须先看一眼 T13 四条硬门禁的结论——以及一条激活后的取证入口。
    """

    @staticmethod
    def _experiment_gate(proposal) -> dict:
        experiment = SkillContentGateService.latest_experiment(proposal)
        return dict(experiment.gate_report or {}) if experiment else {}

    @classmethod
    @transaction.atomic
    def submit_for_approval(cls, *, proposal, actor, reason="", kind: str = "full"):
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法提交审批")
        report = cls._experiment_gate(proposal)
        if not report:
            raise ValidationError("候选尚未执行冻结集门禁，不能提交审批")
        if not report.get("passed"):
            failed = [
                item.get("detail") or item.get("code")
                for item in (report.get("checks") or []) if not item.get("ok")
            ]
            raise ValidationError(
                "候选未通过 T13 硬门禁，不能提交审批：" + "；".join(failed[:5])
            )
        snapshot_id = report.get("snapshot_id")
        snapshot = None
        if snapshot_id:
            from .gate_models import EvaluationGateSnapshot

            snapshot = EvaluationGateSnapshot.objects.filter(pk=snapshot_id).first()
        return CapabilityReleaseService.submit_for_approval(
            version.release, actor=actor, reason=reason,
            gate_snapshot=snapshot, kind=kind,
        )

    @staticmethod
    @transaction.atomic
    def activate(*, proposal, actor, reason="", kind: str = "full"):
        """激活候选版本。门禁结论必须仍然成立。"""
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法激活")
        report = SkillContentLifecycleService._experiment_gate(proposal)
        if not report.get("passed"):
            raise ValidationError("候选未通过 T13 硬门禁，不能激活")
        release = CapabilityReleaseService.promote(version.release, actor=actor, reason=reason)
        proposal.state = "approved"
        proposal.save(update_fields=["state", "updated_at"])
        return release

    @staticmethod
    @transaction.atomic
    def reject(*, proposal, actor, reason):
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法驳回")
        CapabilityReleaseService.reject(version.release, actor=actor, reason=reason)
        proposal.state = "rejected"
        proposal.save(update_fields=["state", "updated_at"])
        return version.release

    @staticmethod
    def observe(*, proposal, window_key, metrics, actor=None, thresholds=None,
                auto_rollback: bool = True):
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法记录观察窗口")
        return CapabilityReleaseService.observe(
            version.release, window_key=window_key, metrics=metrics, actor=actor,
            thresholds=thresholds, auto_rollback=auto_rollback,
        )

    @staticmethod
    def complete_canary(*, proposal, actor, reason="", min_observations=None):
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法结束灰度")
        return CapabilityReleaseService.complete_canary(
            version.release, actor=actor, reason=reason, min_observations=min_observations,
        )

    @staticmethod
    def rollback(*, proposal, actor, reason=""):
        from .capabilities import CapabilityReleaseService

        version = SkillContentGateService._candidate_version(proposal)
        if version is None or version.release is None:
            raise ValidationError("候选尚未派生发布单元，无法回滚")
        return CapabilityReleaseService.rollback(version.release, actor=actor, reason=reason)

    # ------------------------------------------------------------ 取证

    @staticmethod
    def running_flow_evidence(skill) -> dict:
        """激活新版本后，正在运行的流程仍钉在各自锁定的版本上。

        这不是"配置正确"的断言，而是一条可复核的事实：流程锁（以及产出记录的
        ``skill_version``）在发起时就固化了版本 id，激活只改
        ``Skill.active_version`` 这个**解析缓存**，不回溯改写已发出的锁。
        工坊与验收要能拿到这条证据，而不是靠"应该没问题"。
        """
        from skills.models import Skill, SkillVersion
        from .workflow_models import WorkflowSkillLock

        # 取证必须回源：调用方手上的 skill 实例可能是激活前的内存快照，
        # 拿它读 active_version 会把"没生效"报成"已生效"，比不报更糟。
        fresh = Skill.objects.filter(pk=getattr(skill, "pk", None)).first() or skill

        rows = [
            {
                "workflow_id": lock.workflow_id,
                "lock_key": lock.lock_key,
                "skill_version_id": str(lock.skill_version_id or ""),
                "version": getattr(lock.skill_version, "version", ""),
                "package_sha256": lock.package_sha256,
            }
            for lock in WorkflowSkillLock.objects.filter(skill=fresh)
            .select_related("skill_version")
            .order_by("workflow_id", "locked_at")
        ]
        return {
            "skill_id": str(fresh.pk),
            "active_version_id": str(fresh.active_version_id or ""),
            "active_version": (
                SkillVersion.objects.filter(pk=fresh.active_version_id)
                .values_list("version", flat=True).first()
                if fresh.active_version_id else ""
            ),
            "locked_flows": rows,
            "locked_flow_count": len(rows),
        }
