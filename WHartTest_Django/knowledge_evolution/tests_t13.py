"""T13：`skill_content` 候选、评测与上线闭环。

这一层最容易出的四类错，每一类都配了用例：

- **"改了包"变成"改了 active 包"**：派生一旦就地改写生产版本，回滚目标就没了，
  而且运行中的流程会在中途换包。所以专门钉住"基线包零改动 + 新版本不可变"。
- **环境故障被写成内容补丁**：超时、配额这类问题改 ``SKILL.md`` 无效，写进去
  在下一轮评测表现为"改了也没用"，把真正的环境问题掩盖掉。
- **白名单形同虚设**：只拦写路径不拦删除路径、或者不归一化就比，一个 ``./`` 前缀
  或一条 ``remove`` 就能绕过去。
- **均分涨了就放行**：候选整体均分提高但关键回归样本退化，是最典型的"数据好看、
  线上变差"。所以门禁必须逐样本判，而且要用一条"均分确实涨了"的用例来证明它真的拦得住。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.evaluation import CaseComparisonService
from knowledge_evolution.evaluation_models import EvaluationResult, EvaluationRun
from knowledge_evolution.gold_models import GoldCase, GoldDataset, GoldDatasetVersion
from knowledge_evolution.models import EvaluationCase, FeedbackEvent
from knowledge_evolution.optimization import (
    SKILL_CONTENT_CATEGORIES,
    SKILL_CONTENT_TYPE,
    SKILL_CONTENT_TYPES,
    TARGET_TYPES,
    TYPE_BY_CATEGORY,
    OptimizationMaterializationService,
    OptimizationProposalService,
)
from knowledge_evolution.optimization_models import OptimizationProposal
from knowledge_evolution.skill_content import (
    DEFAULT_BADCASE_FLOOR,
    KEY_CASE_FLAGS,
    SKILL_CONTENT_GATE_LABELS,
    SkillContentGateService,
    SkillContentLifecycleService,
    SkillContentOptimizationService,
)
from knowledge_evolution.skill_evolution import (
    SKILL_CONTENT_WHITELIST,
    SkillContentPatchBuilder,
    assert_patch_within_whitelist,
    is_whitelisted_skill_path,
    normalize_package_relative_path,
)
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests
from knowledge_evolution.workflow_models import WorkflowSkillLock
from skills.models import SkillVersion
from skills.runtime import SkillRuntimeResolver
from skills.versions import SkillVersionService

#: 派生候选会真实落盘，必须隔离到临时媒体目录。
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-t13-")

TASK_TYPE = "case_review"

#: 为了让 T09 通用门禁能跑出结论，只放开门禁口径，不放开门禁本身。
GATE_THRESHOLDS = {
    "require_all_partitions": False,
    "min_quality": 0.70,
    "max_quality_regression": 0.05,
}


# =============================================================== 真值：类型与白名单


class SkillContentTruthTests(SimpleTestCase):
    """类型与白名单的唯一真值。"""

    def test_skill_content_is_a_proposal_type(self):
        from knowledge_evolution.optimization_models import OptimizationProposal

        values = [value for value, _label in OptimizationProposal.TYPE_CHOICES]
        self.assertIn(SKILL_CONTENT_TYPE, values)

    def test_skill_content_is_not_category_derived(self):
        """skill_content 是"目标型"候选：同一条归因可由人选择落成哪种候选。

        若它出现在类别映射里，就变成"某类归因只能产出某种候选"，
        把人的判断权换成一张表，也会让 prompt_error 这类既有映射被顶掉。
        """
        self.assertNotIn(SKILL_CONTENT_TYPE, set(TYPE_BY_CATEGORY.values()))
        self.assertEqual(TARGET_TYPES, frozenset({SKILL_CONTENT_TYPE}))
        self.assertEqual(
            SKILL_CONTENT_CATEGORIES,
            frozenset(
                category for category, proposal_type in TYPE_BY_CATEGORY.items()
                if proposal_type in SKILL_CONTENT_TYPES
            ),
        )

    def test_categories_that_need_another_channel_are_excluded(self):
        for category in ("knowledge_missing", "knowledge_stale", "retrieval_error",
                         "environment_error"):
            self.assertNotIn(category, SKILL_CONTENT_CATEGORIES)

    def test_whitelist_covers_content_contract_template_and_checker(self):
        for path in ("SKILL.md", "references/field-rules.md", "references/sub/x.md",
                     "schemas/output.json", "templates/plan.md",
                     "scripts/validate_schema.py", "scripts/check_ok.py"):
            self.assertTrue(is_whitelisted_skill_path(path), path)

    def test_whitelist_rejects_entrypoint_and_arbitrary_paths(self):
        """"改一个包"与"换一个包"的分界：依赖声明、入口脚本、任意文件都不许碰。"""
        for path in ("scripts/main.py", "requirements.txt", "foo/bar.md",
                     "scripts/validate_schema.sh", "templates/app.exe"):
            self.assertFalse(is_whitelisted_skill_path(path), path)

    def test_relative_path_is_normalized_before_matching(self):
        """不归一化就比，``./SKILL.md`` 或反斜杠写法就能绕过白名单。"""
        self.assertEqual(normalize_package_relative_path("./SKILL.md"), "SKILL.md")
        self.assertEqual(
            normalize_package_relative_path("references\\field-rules.md"),
            "references/field-rules.md",
        )
        self.assertTrue(is_whitelisted_skill_path("./references/field-rules.md"))

    def test_escaping_paths_are_refused(self):
        for path in ("../SKILL.md", "references/../../etc/passwd", "/etc/passwd"):
            with self.assertRaises(ValidationError, msg=path):
                normalize_package_relative_path(path)

    def test_whitelist_checks_removals_too(self):
        """只校验写路径，``remove`` 就是一个后门：删掉 SKILL.md 比改它更狠。"""
        with self.assertRaises(ValidationError):
            assert_patch_within_whitelist({"remove": ["scripts/main.py"]})
        self.assertEqual(
            assert_patch_within_whitelist({"remove": ["templates/plan.md"]}),
            ["templates/plan.md"],
        )

    def test_whitelist_has_no_catch_all_pattern(self):
        """白名单里一旦出现通配整包的写法，这道闸门就等于没了。"""
        for pattern in SKILL_CONTENT_WHITELIST:
            self.assertNotIn(pattern, ("*", "**", "**/*"))
            self.assertIn(
                pattern.split("/")[0],
                {"SKILL.md", "references", "schemas", "templates", "scripts"},
                pattern,
            )

    def test_gate_labels_exist_for_every_hard_gate(self):
        for code in ("badcase_fixed", "key_regression_zero_degradation",
                     "schema_passed", "hidden_isolation"):
            self.assertIn(code, SKILL_CONTENT_GATE_LABELS)


# ================================================================= 公共夹具


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillContentBaseTests(SkillHubBaseTests):
    """在公共夹具上补齐"一个可进化的 Skill + 一套可评测的样本"。"""

    def setUp(self):
        super().setUp()
        self.skill, self.baseline = self.make_skill_version(
            name="evolve-review", version="1.0.0", stage=TASK_TYPE,
        )
        # 基线必须是当前活跃版本，否则派生目标就是错的。
        self.skill.active_version = self.baseline
        self.skill.save(update_fields=["active_version", "updated_at"])
        self.baseline.release.state = "active"
        self.baseline.release.save(update_fields=["state", "updated_at"])

        # 归因指向的产出（本轮 Badcase）与"反馈信号来源"的产出刻意分开：
        # 混成同一个，隐藏集隔离那两条判据就会互相掩盖。
        self.attribution_output = self.make_output(task_type=TASK_TYPE)
        self.signal_output = self.make_output(
            task_type=TASK_TYPE, output_hash="signal".ljust(64, "0"),
        )
        FeedbackEvent.objects.create(
            project=self.project, output=self.signal_output,
            signal="defect_confirmed", idempotency_key="t13-signal", actor=self.executor,
        )

    # ------------------------------------------------------------ 构造 helper

    def make_case(self, *, suite, number, split, output=None, metadata=None):
        return EvaluationCase.objects.create(
            suite=suite, case_number=number, task_type=suite.task_type,
            input_payload={"n": number}, split=split, source_output=output,
            metadata=metadata or {},
        )

    def make_run(self, *, suite, specs, name, replay_hash="", config=None):
        """``specs`` 是 ``[(case, score), ...]``，两侧 run 复用同一批 case。"""
        run = EvaluationRun.objects.create(
            suite=suite, name=name, status="completed",
            replay_hash=replay_hash, config=config or {"model": "t13-model", "temperature": 0.1},
        )
        for case, score in specs:
            EvaluationResult.objects.create(
                run=run, case=case, status="completed", l0_score=1.0, l1_score=score,
                latency_ms=100, token_usage=100,
            )
        return run

    def make_suite_with_cases(self, *, key_flags=True):
        """标准布局：gold 1 条 + regression 3 条（1 条目标 + 1 条关键 + 1 条普通）+ fresh 1 条。"""
        suite = self.make_suite(task_type=TASK_TYPE, name="T13 套件")
        cases = {
            "gold": self.make_case(suite=suite, number=1, split="gold", output=self.signal_output),
            "target": self.make_case(
                suite=suite, number=2, split="regression", output=self.attribution_output,
            ),
            "key": self.make_case(
                suite=suite, number=3, split="regression", output=self.signal_output,
                metadata={"key_regression": True} if key_flags else {},
            ),
            "other": self.make_case(
                suite=suite, number=4, split="regression", output=self.signal_output,
            ),
            "fresh": self.make_case(suite=suite, number=5, split="fresh", output=self.signal_output),
        }
        return suite, cases

    def make_gold_version(self, *, state="frozen", name="T13 金标"):
        dataset = GoldDataset.objects.create(
            project=self.project, name=name, task_type=TASK_TYPE, created_by=self.lead,
        )
        return GoldDatasetVersion.objects.create(
            dataset=dataset, version="v1", state=state, content_hash="c" * 64,
            created_by=self.lead, frozen_by=self.lead if state == "frozen" else None,
        )

    def make_gate_runs(self, *, suite, cases, baseline_scores=None, candidate_scores=None,
                       gold=None, replay=True, candidate_config=None,
                       baseline_config=None):
        base_scores = baseline_scores or {
            "gold": 0.90, "target": 0.20, "key": 0.90, "other": 0.90, "fresh": 0.90,
        }
        cand_scores = candidate_scores or {
            "gold": 0.90, "target": 0.95, "key": 0.90, "other": 0.90, "fresh": 0.90,
        }
        replay_hash = (gold.content_hash if (gold is not None and replay) else "zz")
        baseline = self.make_run(
            suite=suite, name="baseline", replay_hash=replay_hash,
            specs=[(cases[name], score) for name, score in base_scores.items()],
            config=baseline_config,
        )
        candidate = self.make_run(
            suite=suite, name="candidate", replay_hash=replay_hash,
            specs=[(cases[name], score) for name, score in cand_scores.items()],
            config=candidate_config,
        )
        return baseline, candidate

    def make_proposal(self, *, attributions, state="draft", change_patch=None,
                      project=None, capability=None, baseline_release=None):
        proposal = OptimizationProposal.objects.create(
            project=project or self.project, capability=capability,
            baseline_release=baseline_release,
            proposal_type=SKILL_CONTENT_TYPE,
            title="Skill 内容候选", summary="测试用",
            change_patch=change_patch or {},
            fingerprint=f"t13-{OptimizationProposal.objects.count()}-{state}",
            state=state, created_by=self.lead,
        )
        proposal.attributions.set(list(attributions))
        return proposal

    def materialize(self, *, proposal, edits=None):
        return SkillContentOptimizationService.materialize(
            proposal=proposal, actor=self.lead, edits=edits,
        )

    def run_gate(self, *, proposal, gold=None):
        suite, cases = self.make_suite_with_cases()
        gold = gold or self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)
        experiment = SkillContentGateService.run(
            proposal=proposal, gold_version=gold,
            baseline_run=baseline, candidate_run=candidate,
            actor=self.lead, thresholds=GATE_THRESHOLDS,
        )
        return {
            "suite": suite, "cases": cases, "gold": gold,
            "baseline": baseline, "candidate": candidate, "experiment": experiment,
        }


# ======================================================= 候选生成（目标型）


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillContentProposalTests(SkillContentBaseTests):
    def test_target_type_keeps_one_proposal_for_the_whole_batch(self):
        """同一个包上的多条归因应落成一个候选，而不是按类别拆成多个补丁。"""
        items = [
            self.make_attribution(output=self.attribution_output, category="prompt_error"),
            self.make_attribution(
                output=self.attribution_output, category="tool_error",
                hypothesis="工具调用前未校验参数",
            ),
        ]
        proposals = OptimizationProposalService.generate(
            attributions=items, actor=self.lead, target_type=SKILL_CONTENT_TYPE,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].proposal_type, SKILL_CONTENT_TYPE)
        self.assertEqual(proposals[0].attributions.count(), 2)

    def test_unconfirmed_attribution_is_refused(self):
        item = self.make_attribution(state="proposed")
        with self.assertRaises(ValidationError):
            OptimizationProposalService.generate(
                attributions=[item], actor=self.lead, target_type=SKILL_CONTENT_TYPE,
            )

    def test_category_needing_another_channel_is_refused_with_a_hint(self):
        item = self.make_attribution(category="knowledge_missing", hypothesis="知识缺失")
        with self.assertRaises(ValidationError) as ctx:
            OptimizationProposalService.generate(
                attributions=[item], actor=self.lead, target_type=SKILL_CONTENT_TYPE,
            )
        self.assertIn("知识候选", "".join(ctx.exception.messages))

    def test_unknown_target_type_is_refused(self):
        item = self.make_attribution(output=self.attribution_output)
        with self.assertRaises(ValidationError):
            OptimizationProposalService.generate(
                attributions=[item], actor=self.lead, target_type="not_a_type",
            )

    def test_default_generation_is_unchanged(self):
        """不改默认路径：不传 target_type 时仍按类别映射分流。"""
        item = self.make_attribution(output=self.attribution_output, category="prompt_error")
        proposals = OptimizationProposalService.generate(attributions=[item], actor=self.lead)
        self.assertEqual([p.proposal_type for p in proposals], ["prompt"])


# =========================================================== 派生候选包


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillContentDeriveTests(SkillContentBaseTests):
    def test_creates_new_immutable_version_without_touching_active(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        before_content = (Path(self.baseline.get_full_path()) / "SKILL.md").read_text(
            encoding="utf-8",
        )
        before_sha = self.baseline.package_sha256

        result = self.materialize(proposal=proposal)

        candidate = result["candidate"]
        self.assertNotEqual(candidate.pk, self.baseline.pk)
        self.assertNotEqual(candidate.version, self.baseline.version)
        self.assertEqual(candidate.previous_version_id, self.baseline.pk)
        self.assertEqual(candidate.source_type, "evolution")
        self.assertEqual(candidate.state, "draft")
        self.assertTrue(result["active_untouched"])

        # 基线包一个字节都没变，且表与盘都指向同一个事实。
        self.baseline.refresh_from_db()
        self.assertEqual(self.baseline.package_sha256, before_sha)
        self.assertTrue(SkillVersionService.verify_package_integrity(self.baseline)["ok"])
        self.assertEqual(
            (Path(self.baseline.get_full_path()) / "SKILL.md").read_text(encoding="utf-8"),
            before_content,
        )
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.active_version_id, self.baseline.pk)

    def test_patch_records_whitelist_paths_and_rollback_target(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        result = self.materialize(proposal=proposal)

        self.assertEqual(result["rollback_target"], str(self.baseline.pk))
        self.assertIn("SKILL.md", (result["patch"] or {}).get("whitelist_paths", []))
        plan = SkillContentGateService.summary(proposal)
        self.assertEqual(plan["baseline_skill_version_id"], str(self.baseline.pk))
        self.assertEqual(plan["candidate_skill_version_id"], str(result["candidate"].pk))
        self.assertEqual(plan["rollback_plan"]["strategy"], "rollback_skill_release")
        self.assertTrue(plan["risk_notes"])

    def test_explicit_edits_can_target_references(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        result = self.materialize(proposal=proposal, edits=[{
            "path": "references/field-rules.md",
            "content": "# 字段口径\n\n- 投票编号必填\n",
        }])

        candidate_root = Path(result["candidate"].get_full_path())
        self.assertTrue((candidate_root / "references" / "field-rules.md").is_file())
        self.assertEqual(
            result["patch"]["whitelist_paths"], ["references/field-rules.md"],
        )

    def test_edits_outside_whitelist_leave_no_trace(self):
        """不合格的候选不该在库里留下任何痕迹。"""
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        count_before = SkillVersion.objects.filter(skill=self.skill).count()

        with self.assertRaises(ValidationError):
            self.materialize(proposal=proposal, edits=[{
                "path": "scripts/main.py", "content": "# 劫持入口\n",
            }])

        self.assertEqual(SkillVersion.objects.filter(skill=self.skill).count(), count_before)

    def test_environment_attributions_are_excluded_not_fatal(self):
        """环境故障与真实内容问题常常并存：剔除环境那条，其余照常派生。"""
        usable = self.make_attribution(
            output=self.attribution_output, category="prompt_error",
        )
        env = self.make_attribution(
            output=self.attribution_output, category="environment_error",
            hypothesis="执行超时",
        )
        proposal = self.make_proposal(
            attributions=[usable, env],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        result = self.materialize(proposal=proposal)

        self.assertTrue(result["candidate"].pk)
        excluded = proposal.change_patch["excluded_attribution_ids"]
        self.assertEqual(excluded, [str(env.pk)])

    def test_all_environment_attributions_are_refused(self):
        env = self.make_attribution(
            output=self.attribution_output, category="environment_error",
            hypothesis="执行超时",
        )
        proposal = self.make_proposal(
            attributions=[env],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        with self.assertRaises(ValidationError) as ctx:
            self.materialize(proposal=proposal)
        self.assertIn("环境", "".join(ctx.exception.messages))

    def test_unconfirmed_attribution_is_refused_at_derivation(self):
        item = self.make_attribution(output=self.attribution_output, state="proposed")
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        with self.assertRaises(ValidationError):
            self.materialize(proposal=proposal)

    def test_prohibited_gold_sample_is_refused(self):
        item = self.make_attribution(output=self.attribution_output)
        # 用未冻结版本承载样本：冻结版本的内容是不可改的，样本只能在冻结前落进去。
        editable = self.make_gold_version(state="draft", name="T13 待冻结金标")
        GoldCase.objects.create(
            version=editable, source_output=self.attribution_output,
            task_type=TASK_TYPE, title="禁止优化的样本", source_hash="h" * 64,
            allow_optimization=False, created_by=self.lead,
        )
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        with self.assertRaises(ValidationError) as ctx:
            self.materialize(proposal=proposal)
        self.assertIn("禁止用于优化", "".join(ctx.exception.messages))

    def test_materialize_is_idempotent(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        first = self.materialize(proposal=proposal)
        second = self.materialize(proposal=proposal)

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["candidate"].pk, first["candidate"].pk)

    def test_baseline_must_be_the_active_version(self):
        """基线不是活跃版本时派生目标就是错的：新包会基于一份已经不在生产的包。"""
        self.make_skill_version(name="evolve-review", version="1.0.1", stage=TASK_TYPE)
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        # 把活跃版本改到别处，模拟"引擎线已经换版"。
        newer = SkillVersion.objects.filter(skill=self.skill).exclude(pk=self.baseline.pk).first()
        self.skill.active_version = newer
        self.skill.save(update_fields=["active_version", "updated_at"])

        with self.assertRaises(ValidationError):
            self.materialize(proposal=proposal)

    def test_cross_project_baseline_is_refused(self):
        item = self.make_attribution(output=self.attribution_output)
        _foreign_skill, foreign_version = self.make_skill_version(
            name="foreign-review", version="1.0.0", stage=TASK_TYPE,
            project=self.other_project,
        )
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(foreign_version.id)},
        )
        with self.assertRaises(ValidationError):
            self.materialize(proposal=proposal)

    def test_missing_baseline_is_reported(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(attributions=[item])
        with self.assertRaises(ValidationError) as ctx:
            self.materialize(proposal=proposal)
        self.assertIn("基线", "".join(ctx.exception.messages))

    def test_default_patch_builder_writes_the_managed_guardrail(self):
        """默认策略必须落在 SKILL.md 的受管小节上：可反复派生而不往后堆。"""
        patch = SkillContentPatchBuilder.build_patch(
            skill_version=self.baseline,
            attributions=[self.make_attribution(output=self.attribution_output)],
        )
        self.assertEqual(patch["strategy"], "append_guardrail_section")
        self.assertEqual(patch["whitelist_paths"], ["SKILL.md"])

    def test_materialize_through_the_generic_entry_point(self):
        """通用 materialize 入口对 skill_content 也返回发布单元（前端不必分叉）。"""
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(
            attributions=[item],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )

        release = OptimizationMaterializationService.materialize(
            proposal=proposal, actor=self.lead,
        )

        self.assertEqual(release.kind, "skill")
        self.assertEqual(release.state, "draft")


# ============================================================ 逐样本硬门禁


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BadcaseGateTests(SkillContentBaseTests):
    def test_fixed_badcase_passes(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(attributions=[item])
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.badcase_report(
            proposal=proposal, baseline_run=baseline, candidate_run=candidate,
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["floor"], DEFAULT_BADCASE_FLOOR)
        self.assertEqual(len(report["cases"]), 1)

    def test_unfixed_badcase_blocks(self):
        item = self.make_attribution(output=self.attribution_output)
        proposal = self.make_proposal(attributions=[item])
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases,
            candidate_scores={"gold": 0.9, "target": 0.5, "key": 0.9, "other": 0.9, "fresh": 0.9},
        )

        report = SkillContentGateService.badcase_report(
            proposal=proposal, baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertIn("未修复", report["detail"])

    def test_badcase_without_a_frozen_sample_blocks(self):
        """"修好了"必须能被证明；没有对应样本就不是"通过"，而是"无法判定"。"""
        other_output = self.make_output(task_type=TASK_TYPE, output_hash="o".ljust(64, "0"))
        item = self.make_attribution(output=other_output)
        proposal = self.make_proposal(attributions=[item])
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.badcase_report(
            proposal=proposal, baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertIn("冻结评测集", report["detail"])

    def test_badcase_report_carries_the_floor_for_the_page(self):
        """页面要能解释"为什么算没修好"，所以下限必须随结论一起给出。"""
        proposal = self.make_proposal(attributions=[self.make_attribution()])
        report = SkillContentGateService.badcase_report(
            proposal=proposal, baseline_run=None, candidate_run=None,
        )
        self.assertFalse(report["ok"])
        self.assertIn("冻结评测集", report["detail"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class KeyRegressionGateTests(SkillContentBaseTests):
    def test_annotated_key_case_has_priority(self):
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.key_regression_report(
            baseline_run=baseline, candidate_run=candidate,
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["source"], "annotated")
        self.assertEqual(report["key_case_ids"], [str(cases["key"].pk)])
        self.assertIn(KEY_CASE_FLAGS[0], KEY_CASE_FLAGS)

    def test_key_case_degradation_blocks(self):
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases,
            candidate_scores={"gold": 0.9, "target": 0.95, "key": 0.5, "other": 0.9, "fresh": 0.9},
        )

        report = SkillContentGateService.key_regression_report(
            baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual([row["case_id"] for row in report["degraded"]], [str(cases["key"].pk)])

    def test_falls_back_to_regression_cases_the_baseline_passed(self):
        """一条都没标时退化为"基线已通过的回归样本"，否则没人标就等于没门禁。"""
        suite, cases = self.make_suite_with_cases(key_flags=False)
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases,
            candidate_scores={"gold": 0.9, "target": 0.95, "key": 0.5, "other": 0.9, "fresh": 0.9},
        )

        report = SkillContentGateService.key_regression_report(
            baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["source"], "baseline_passed")
        self.assertIn(str(cases["key"].pk), report["key_case_ids"])

    def test_missing_key_cases_block_instead_of_passing(self):
        """"没有可判定的回归基线"与"回归没退化"是两件事。"""
        suite = self.make_suite(task_type=TASK_TYPE, name="只有一条失败回归样本")
        target = self.make_case(
            suite=suite, number=1, split="regression", output=self.attribution_output,
        )
        gold = self.make_case(suite=suite, number=2, split="gold", output=self.signal_output)
        fresh = self.make_case(suite=suite, number=3, split="fresh", output=self.signal_output)
        baseline = self.make_run(
            suite=suite, name="baseline",
            specs=[(gold, 0.2), (target, 0.2), (fresh, 0.2)],
        )
        candidate = self.make_run(
            suite=suite, name="candidate",
            specs=[(gold, 0.2), (target, 0.95), (fresh, 0.2)],
        )

        report = SkillContentGateService.key_regression_report(
            baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["key_case_ids"], [])

    def test_missing_candidate_side_counts_as_degradation(self):
        suite, cases = self.make_suite_with_cases()
        baseline = self.make_run(
            suite=suite, name="baseline",
            specs=[(cases[name], 0.9) for name in cases],
        )
        candidate = self.make_run(
            suite=suite, name="candidate",
            specs=[(cases["gold"], 0.9), (cases["target"], 0.95), (cases["fresh"], 0.9)],
        )

        report = SkillContentGateService.key_regression_report(
            baseline_run=baseline, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["degraded"][0]["candidate"], None)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SchemaAndIsolationGateTests(SkillContentBaseTests):
    def _candidate_for(self, proposal):
        return self.materialize(proposal=proposal)["candidate"]

    def test_valid_package_passes_schema_gate(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        candidate = self._candidate_for(proposal)

        report = SkillContentGateService.schema_report(candidate)

        self.assertTrue(report["ok"])
        self.assertEqual(report["package_sha256"], candidate.package_sha256)

    def test_tampered_package_fails_schema_gate(self):
        """包被事后改写过就必须拦下：审批看到的那份内容已经不是这份。"""
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        candidate = self._candidate_for(proposal)
        target = Path(candidate.get_full_path()) / "SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\n被改写的行\n", encoding="utf-8")

        report = SkillContentGateService.schema_report(candidate)

        self.assertFalse(report["ok"])
        self.assertIn("完整性", report["detail"])

    def test_hidden_partition_cannot_be_the_optimization_target(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
        )
        suite, cases = self.make_suite_with_cases()
        cases["target"].split = "hidden"
        cases["target"].save(update_fields=["split"])
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.hidden_isolation_report(
            proposal=proposal, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["leaked_case_ids"], [str(cases["target"].pk)])

    def test_hidden_case_sharing_the_target_output_is_caught(self):
        """换个 case 记录指向同一份产出，等于绕开上一条判据，所以按产出再查一次。"""
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
        )
        suite, cases = self.make_suite_with_cases()
        self.make_case(
            suite=suite, number=6, split="hidden", output=self.attribution_output,
        )
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.hidden_isolation_report(
            proposal=proposal, candidate_run=candidate,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["leaked_output_ids"], [str(self.attribution_output.pk)])

    def test_clean_layout_passes_isolation(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
        )
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases)

        report = SkillContentGateService.hidden_isolation_report(
            proposal=proposal, candidate_run=candidate,
        )

        self.assertTrue(report["ok"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class FreezeGateTests(SkillContentBaseTests):
    def test_matching_replay_and_config_passes(self):
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)

        report = SkillContentGateService.freeze_report(
            baseline_run=baseline, candidate_run=candidate, gold_version=gold,
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["replay_hash"], gold.content_hash)

    def test_unfrozen_gold_version_blocks(self):
        suite, cases = self.make_suite_with_cases()
        # 直接造一个未冻结的版本：冻结版本本身是不允许被改回 labeling 的。
        gold = self.make_gold_version(state="labeling")
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)

        report = SkillContentGateService.freeze_report(
            baseline_run=baseline, candidate_run=candidate, gold_version=gold,
        )

        self.assertFalse(report["ok"])
        self.assertIn("尚未冻结", report["detail"])

    def test_replay_hash_mismatch_blocks(self):
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)
        candidate.replay_hash = "not-the-frozen-hash"
        candidate.save(update_fields=["replay_hash", "updated_at"])

        report = SkillContentGateService.freeze_report(
            baseline_run=baseline, candidate_run=candidate, gold_version=gold,
        )

        self.assertFalse(report["ok"])
        self.assertIn("冻结金标", report["detail"])

    def test_model_config_mismatch_blocks(self):
        """两侧模型配置不同，指标就没有可比性：候选更好可能只是这次温度更低。"""
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases, gold=gold,
            candidate_config={"model": "t13-model", "temperature": 0.9},
        )

        report = SkillContentGateService.freeze_report(
            baseline_run=baseline, candidate_run=candidate, gold_version=gold,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["mismatched_keys"], ["temperature"])


# ====================================================== 门禁汇总与晋级


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillContentGateRunTests(SkillContentBaseTests):
    def test_all_gates_pass_and_wait_for_approval(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        self.materialize(proposal=proposal)

        outcome = self.run_gate(proposal=proposal)

        experiment = outcome["experiment"]
        self.assertEqual(experiment.status, "passed")
        self.assertTrue(experiment.gate_report["passed"])
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "awaiting_approval")
        codes = {item["code"] for item in experiment.gate_report["checks"]}
        self.assertTrue({
            "general_gate", "badcase", "key_regression", "schema",
            "hidden_isolation", "freeze",
        } <= codes)

    def test_higher_average_but_key_regression_blocks(self):
        """验收里的核心一条：均分确实涨了，但关键回归样本退化 → 阻止晋级。

        用显式断言钉住"均分更高"这个前提，否则这条用例在将来指标口径变化后
        会退化成"随便一个失败案例"，失去它本该证明的东西。
        """
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        self.materialize(proposal=proposal)
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases, gold=gold,
            baseline_scores={"gold": 0.5, "target": 0.2, "key": 0.9, "other": 0.9, "fresh": 0.5},
            candidate_scores={"gold": 0.95, "target": 0.95, "key": 0.6, "other": 0.6, "fresh": 0.95},
        )

        from knowledge_evolution.evaluation_gates import RunMetricsService

        self.assertGreater(
            RunMetricsService.summarize(candidate)["quality"],
            RunMetricsService.summarize(baseline)["quality"],
        )

        experiment = SkillContentGateService.run(
            proposal=proposal, gold_version=gold, baseline_run=baseline,
            candidate_run=candidate, actor=self.lead, thresholds=GATE_THRESHOLDS,
        )

        self.assertEqual(experiment.status, "failed")
        failed = {
            item["code"] for item in experiment.gate_report["checks"] if not item["ok"]
        }
        self.assertIn("key_regression", failed)
        self.assertNotIn("general_gate", failed)
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "draft")

    def test_gate_without_a_candidate_version_is_refused(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
        )
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)

        with self.assertRaises(ValidationError):
            SkillContentGateService.run(
                proposal=proposal, gold_version=gold, baseline_run=baseline,
                candidate_run=candidate, actor=self.lead, thresholds=GATE_THRESHOLDS,
            )


# ================================================ 审批 / 激活 / 观察 / 回滚


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class SkillContentLifecycleTests(SkillContentBaseTests):
    def _prepared(self, *, gate=True):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        candidate = self.materialize(proposal=proposal)["candidate"]
        outcome = self.run_gate(proposal=proposal) if gate else None
        return proposal, candidate, outcome

    def _lock_baseline(self):
        return WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-t13", lock_key=TASK_TYPE,
            skill=self.skill, skill_version=self.baseline,
            package_sha256=self.baseline.package_sha256, locked_by=self.lead,
        )

    def test_submit_before_running_the_gate_is_refused(self):
        proposal, _candidate, _outcome = self._prepared(gate=False)
        with self.assertRaises(ValidationError) as ctx:
            SkillContentLifecycleService.submit_for_approval(
                proposal=proposal, actor=self.lead,
            )
        self.assertIn("冻结集门禁", "".join(ctx.exception.messages))

    def test_submit_after_a_failed_gate_is_refused(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        self.materialize(proposal=proposal)
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases, gold=gold,
            candidate_scores={"gold": 0.9, "target": 0.5, "key": 0.9, "other": 0.9, "fresh": 0.9},
        )
        SkillContentGateService.run(
            proposal=proposal, gold_version=gold, baseline_run=baseline,
            candidate_run=candidate, actor=self.lead, thresholds=GATE_THRESHOLDS,
        )

        with self.assertRaises(ValidationError) as ctx:
            SkillContentLifecycleService.submit_for_approval(
                proposal=proposal, actor=self.lead,
            )
        self.assertIn("硬门禁", "".join(ctx.exception.messages))

    def test_activate_after_a_failed_gate_is_refused(self):
        proposal = self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch={"baseline_skill_version_id": str(self.baseline.id)},
        )
        self.materialize(proposal=proposal)
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases, gold=gold,
            candidate_scores={"gold": 0.9, "target": 0.5, "key": 0.9, "other": 0.9, "fresh": 0.9},
        )
        SkillContentGateService.run(
            proposal=proposal, gold_version=gold, baseline_run=baseline,
            candidate_run=candidate, actor=self.lead, thresholds=GATE_THRESHOLDS,
        )

        with self.assertRaises(ValidationError):
            SkillContentLifecycleService.activate(proposal=proposal, actor=self.lead)

    def test_submit_then_activate_promotes_the_candidate(self):
        proposal, candidate, _outcome = self._prepared()

        release = SkillContentLifecycleService.submit_for_approval(
            proposal=proposal, actor=self.lead, reason="门禁通过",
        )
        self.assertEqual(release.state, "awaiting_approval")
        self.assertTrue(release.approval_snapshot_hash)

        active = SkillContentLifecycleService.activate(
            proposal=proposal, actor=self.lead, reason="批准上线",
        )

        self.assertEqual(active.state, "active")
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.active_version_id, candidate.pk)
        self.baseline.release.refresh_from_db()
        self.assertEqual(self.baseline.release.state, "retired")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "approved")

    def test_activation_does_not_touch_locked_running_flows(self):
        """新版本激活只改解析缓存，不回溯改写已发出的流程锁。"""
        proposal, candidate, _outcome = self._prepared()
        lock = self._lock_baseline()

        SkillContentLifecycleService.submit_for_approval(proposal=proposal, actor=self.lead)
        SkillContentLifecycleService.activate(proposal=proposal, actor=self.lead)

        lock.refresh_from_db()
        self.assertEqual(lock.skill_version_id, self.baseline.pk)
        resolved = SkillRuntimeResolver.resolve_locked(lock)
        self.assertEqual(resolved.pk, self.baseline.pk)

        evidence = SkillContentLifecycleService.running_flow_evidence(self.skill)
        self.assertEqual(evidence["active_version_id"], str(candidate.pk))
        self.assertEqual(evidence["locked_flow_count"], 1)
        self.assertEqual(evidence["locked_flows"][0]["version"], self.baseline.version)

    def test_rollback_restores_the_baseline_release(self):
        proposal, candidate, _outcome = self._prepared()
        SkillContentLifecycleService.submit_for_approval(proposal=proposal, actor=self.lead)
        SkillContentLifecycleService.activate(proposal=proposal, actor=self.lead)

        restored = SkillContentLifecycleService.rollback(
            proposal=proposal, actor=self.lead, reason="生产指标超阈值",
        )

        restored.refresh_from_db()
        self.assertEqual(restored.state, "active")
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.active_version_id, self.baseline.pk)
        candidate.release.refresh_from_db()
        self.assertEqual(candidate.release.state, "rolled_back")

    def test_reject_requires_and_records_a_reason(self):
        proposal, candidate, _outcome = self._prepared()
        SkillContentLifecycleService.submit_for_approval(proposal=proposal, actor=self.lead)

        release = SkillContentLifecycleService.reject(
            proposal=proposal, actor=self.lead, reason="护栏描述与业务口径不一致",
        )

        self.assertEqual(release.state, "rejected")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "rejected")
        # candidate 是本方法内 materialize 拿到的独立实例，其 release 是内存里的陈旧对象，
        # 断言前必须回库刷新，否则会拿到 reject 之前的 state。
        self.assertEqual(candidate.release_id, release.pk)
        stale = candidate.release
        stale.refresh_from_db()
        self.assertEqual(stale.state, "rejected")

    def test_observation_breach_rolls_back_automatically(self):
        proposal, _candidate, _outcome = self._prepared()
        SkillContentLifecycleService.submit_for_approval(proposal=proposal, actor=self.lead)
        SkillContentLifecycleService.activate(proposal=proposal, actor=self.lead)

        payload = SkillContentLifecycleService.observe(
            proposal=proposal, window_key="w1",
            metrics={"l1_score": 0.3, "latency_regression": 0.9},
            actor=self.lead,
            thresholds={"min_l1_score": 0.7, "max_latency_regression": 0.2},
        )

        self.assertTrue(payload["rolled_back"])
        self.assertEqual(payload["state"], "rolled_back")
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.active_version_id, self.baseline.pk)


# ================================================================ API


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class T13ApiTests(SkillContentBaseTests):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.lead)
        self.base = "/api/knowledge-evolution/optimization-proposals/"

    def _proposal(self, *, change_patch=None):
        return self.make_proposal(
            attributions=[self.make_attribution(output=self.attribution_output)],
            change_patch=change_patch or {"baseline_skill_version_id": str(self.baseline.id)},
        )

    def _gate_payload(self, *, gold, baseline, candidate):
        return {
            "gold_dataset_version": str(gold.pk),
            "baseline_run": str(baseline.pk),
            "candidate_run": str(candidate.pk),
            "thresholds": GATE_THRESHOLDS,
        }

    def test_generate_with_target_type(self):
        item = self.make_attribution(output=self.attribution_output)
        response = self.client.post(
            f"{self.base}generate/",
            {"attribution_ids": [str(item.pk)], "target_type": SKILL_CONTENT_TYPE},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data[0]["proposal_type"], SKILL_CONTENT_TYPE)

    def test_plan_reports_missing_conditions_before_derivation(self):
        proposal = self._proposal()
        response = self.client.get(f"{self.base}{proposal.pk}/skill-content-plan/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["candidate_skill_version_id"], "")
        self.assertFalse(response.data["gate_passed"])

    def test_materialize_endpoint_is_idempotent(self):
        proposal = self._proposal()
        url = f"{self.base}{proposal.pk}/skill-content-materialize/"

        first = self.client.post(url, {}, format="json")
        second = self.client.post(url, {}, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["reused"])
        self.assertEqual(
            first.data["candidate_skill_version_id"], second.data["candidate_skill_version_id"],
        )
        self.assertTrue(first.data["active_untouched"])

    def test_evaluate_then_submit_then_activate(self):
        proposal = self._proposal()
        self.client.post(f"{self.base}{proposal.pk}/skill-content-materialize/", {}, format="json")
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)

        evaluated = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-evaluate/",
            self._gate_payload(gold=gold, baseline=baseline, candidate=candidate),
            format="json",
        )
        self.assertEqual(evaluated.status_code, 200)
        self.assertEqual(evaluated.data["status"], "passed")

        submitted = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-submit-approval/",
            {"reason": "门禁通过"}, format="json",
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["state"], "awaiting_approval")

        activated = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-activate/", {}, format="json",
        )
        self.assertEqual(activated.status_code, 200)
        self.assertEqual(activated.data["state"], "active")

    def test_evaluate_without_derivation_is_refused(self):
        proposal = self._proposal()
        suite, cases = self.make_suite_with_cases()
        gold = self.make_gold_version()
        baseline, candidate = self.make_gate_runs(suite=suite, cases=cases, gold=gold)

        response = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-evaluate/",
            self._gate_payload(gold=gold, baseline=baseline, candidate=candidate),
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_submit_before_gate_returns_400(self):
        proposal = self._proposal()
        self.client.post(f"{self.base}{proposal.pk}/skill-content-materialize/", {}, format="json")

        response = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-submit-approval/", {}, format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_running_flows_endpoint_returns_evidence(self):
        proposal = self._proposal()
        self.client.post(f"{self.base}{proposal.pk}/skill-content-materialize/", {}, format="json")
        WorkflowSkillLock.objects.create(
            project=self.project, workflow_id="wf-api-t13", lock_key=TASK_TYPE,
            skill=self.skill, skill_version=self.baseline,
            package_sha256=self.baseline.package_sha256, locked_by=self.lead,
        )

        response = self.client.get(f"{self.base}{proposal.pk}/skill-content-running-flows/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["locked_flow_count"], 1)
        self.assertEqual(response.data["locked_flows"][0]["skill_version_id"], str(self.baseline.pk))

    def test_plan_on_a_non_skill_content_proposal_returns_400(self):
        proposal = OptimizationProposal.objects.create(
            project=self.project, proposal_type="prompt", title="Prompt 候选",
            summary="x", fingerprint="t13-api-other", created_by=self.lead,
        )
        response = self.client.get(f"{self.base}{proposal.pk}/skill-content-plan/")
        self.assertEqual(response.status_code, 400)

    def test_executor_cannot_materialize(self):
        proposal = self._proposal()
        self.client.force_authenticate(self.executor)
        response = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-materialize/", {}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_read_the_plan(self):
        proposal = self._proposal()
        outsider = self.make_outsider()
        self.client.force_authenticate(outsider)
        response = self.client.get(f"{self.base}{proposal.pk}/skill-content-plan/")
        self.assertIn(response.status_code, (403, 404))

    def make_outsider(self):
        from django.contrib.auth.models import User

        return User.objects.create_user(username="t13-outsider", password="x")

    def test_cross_project_evaluate_payload_is_not_found(self):
        proposal = self._proposal()
        self.client.post(f"{self.base}{proposal.pk}/skill-content-materialize/", {}, format="json")
        self.make_skill_version(
            name="foreign-t13", version="1.0.0", stage=TASK_TYPE, project=self.other_project,
        )
        suite = self.make_suite(task_type=TASK_TYPE, name="他项目套件", project=self.other_project)
        output = self.make_output(task_type=TASK_TYPE, project=self.other_project)
        case = self.make_case(suite=suite, number=1, split="gold", output=output)
        run = self.make_run(suite=suite, name="foreign", specs=[(case, 0.9)])
        gold_version = self.make_gold_version()

        response = self.client.post(
            f"{self.base}{proposal.pk}/skill-content-evaluate/",
            {
                "gold_dataset_version": str(gold_version.pk),
                "baseline_run": str(run.pk),
                "candidate_run": str(run.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)


# ============================================================ 逐样本对比底座


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class CaseComparisonTests(SkillContentBaseTests):
    def test_score_map_skips_cases_without_a_score(self):
        """记 0 会把"这条没跑"伪装成"这条退化了"，在关键回归门禁上产生假警报。"""
        suite = self.make_suite(task_type=TASK_TYPE, name="缺分数")
        case = self.make_case(suite=suite, number=1, split="regression")
        run = self.make_run(suite=suite, name="r", specs=[(case, 0.9)])
        EvaluationResult.objects.filter(run=run).update(l1_score=None)

        self.assertEqual(CaseComparisonService.score_map(run), {})

    def test_compare_cases_sorts_worst_first(self):
        suite, cases = self.make_suite_with_cases()
        baseline, candidate = self.make_gate_runs(
            suite=suite, cases=cases,
            candidate_scores={"gold": 0.9, "target": 0.95, "key": 0.5, "other": 0.9, "fresh": 0.9},
        )

        rows = CaseComparisonService.compare_cases(
            baseline_run=baseline, candidate_run=candidate, split="regression",
        )

        self.assertEqual(rows[0]["case_id"], str(cases["key"].pk))
        self.assertAlmostEqual(rows[0]["delta"], -0.4, places=6)
