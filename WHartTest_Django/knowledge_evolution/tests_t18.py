"""T18：Skill Hub 生产控制台的后端面——治理读口、缺失条件与状态推进。

测试组织原则同 T09–T16：**每个用例只证明一件在真实控制台上会出错的事**。

控制台最容易出的两类错，正是这里要钉死的：

1. **按钮亮着但一点就报错**。根因是前端另拼一套"能不能点"的判据，与后端校验
   分叉。所以 ``missing_conditions`` 只有一份真值，接口原样透出，前端只渲染。
   本文件同时断言"接口说不能的，服务层也真的不能"，两头对齐才算过。
2. **候选永远停在草稿**。T18 之前没有任何代码路径驱动 ``draft``/``validating``
   两格，于是新建的候选永远进不了评测与审批。这里覆盖推进、幂等、失败转驳回，
   以及"版本目录被改动后重新校验必须发现"。

另外覆盖跨项目不可见（用 404 而不是 403，不泄露存在性）与角色边界
（验评类动作执行人员可做，审批/驳回/隔离/回滚仅测试负责人）。
"""
from __future__ import annotations

from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from knowledge_evolution.capability_models import CapabilityDefinition
from knowledge_evolution.capabilities import CapabilityReleaseService, can_reach
from knowledge_evolution.gate_models import EvaluationGateSnapshot
from knowledge_evolution.knowledge_models import KnowledgeAuditLog
from knowledge_evolution.tests_t09_t13 import TEST_MEDIA_ROOT, SkillHubBaseTests
from skills.versions import SkillVersionService

RELEASES_URL = "/api/knowledge-evolution/capability-releases/"


def _snapshot(release, *, passed: bool, checks: list | None = None,
              content_hash: str = "a" * 64):
    """直接落一份门禁快照。

    失败路径（缺条件、逐条 check 未过）刻意手工构造：用真实 ``run_gate`` 去制造
    "失败"会引入十几个可能的失败原因，测试就无法指出到底修哪儿。真实门禁的
    通过路径另有 ``make_passing_gate`` 覆盖，两条路各司其职。
    """
    return EvaluationGateSnapshot.objects.create(
        project=release.project, release=release, kind="full",
        candidate_run=None, baseline_run=None,
        partitions={}, metrics={}, checks=checks or [],
        passed=passed, content_hash=content_hash, created_by=release.created_by,
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class T18BaseTests(SkillHubBaseTests):
    def _candidate(self, *, name="clarity-review", version="1.0.0",
                   stage="case_review", project=None):
        """造一个停在 `draft` 的候选版本（T06 语义：新建一律草稿）。"""
        _, skill_version = self.make_skill_version(
            name=name, version=version, stage=stage, project=project,
        )
        return skill_version

    def _validated(self, **kwargs):
        """候选推进到 `shadow`（静态校验已过）。"""
        version = self._candidate(**kwargs)
        SkillVersionService.submit_for_validation(version, actor=self.lead)
        version.refresh_from_db()
        self.assertEqual(version.state, "shadow")
        return version

    def _awaiting_approval(self, **kwargs):
        """候选走到 `awaiting_approval`（门禁通过并提交审批）。"""
        version = self._validated(**kwargs)
        self.make_passing_gate(release=version.release)
        CapabilityReleaseService.submit_for_approval(
            version.release, actor=self.lead, reason="门禁通过",
        )
        version.refresh_from_db()
        self.assertEqual(version.state, "awaiting_approval")
        return version

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class VersionStaticValidationTests(T18BaseTests):
    """`versions/{id}/validate/`：把候选从草稿推进到可评测。"""

    def test_validate_advances_draft_to_shadow(self):
        version = self._candidate()
        self.assertEqual(version.state, "draft")

        report, _ = SkillVersionService.submit_for_validation(version, actor=self.lead)

        version.refresh_from_db()
        self.assertEqual(version.state, "shadow")
        self.assertTrue(report.ok, [i.as_dict() for i in report.errors])

    def test_validate_writes_a_validate_audit_entry(self):
        """校验必须留痕：审计里要能看出候选被谁、以什么结论送进了哪一格。"""
        version = self._candidate()
        before = KnowledgeAuditLog.objects.filter(
            project=self.project, action="validate",
        ).count()

        SkillVersionService.submit_for_validation(version, actor=self.lead, reason="首次送审")

        logs = KnowledgeAuditLog.objects.filter(project=self.project, action="validate")
        self.assertEqual(logs.count(), before + 1)
        entry = logs.first()
        self.assertEqual(entry.actor, self.lead)
        # 审计记的是**结论落在哪一格**（不是中间态 validating），否则时间线读不出结果。
        self.assertEqual(entry.to_state, "shadow")
        self.assertEqual(entry.reason, "首次送审")
        self.assertTrue(entry.detail["ok"])

    def test_failed_validation_still_keeps_its_evidence(self):
        """校验不通过时，驳回结论与审计必须留下来。

        这是最容易被写错的一处：把状态写入和 raise 放进同一个事务，抛异常会把
        刚写下的证据一起回滚，控制台就只剩"草稿"，用户看不到为什么没过。
        """
        version = self._candidate()
        package_dir = Path(version.get_full_path())
        (package_dir / "extra.md").write_text("被塞进来的文件\n", encoding="utf-8")

        with self.assertRaises(ValidationError):
            SkillVersionService.submit_for_validation(version, actor=self.lead, reason="复核")

        version.refresh_from_db()
        self.assertEqual(version.state, "rejected")
        entry = KnowledgeAuditLog.objects.filter(
            project=self.project, action="validate",
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.to_state, "rejected")
        self.assertFalse(entry.detail["ok"])

    def test_validate_is_idempotent_on_a_version_already_past_it(self):
        """重复提交不得回退状态，也不得再落一条审计。"""
        version = self._validated()
        logs = KnowledgeAuditLog.objects.filter(project=self.project, action="validate").count()

        SkillVersionService.submit_for_validation(version, actor=self.lead)

        version.refresh_from_db()
        self.assertEqual(version.state, "shadow")
        self.assertEqual(
            KnowledgeAuditLog.objects.filter(project=self.project, action="validate").count(),
            logs,
        )

    def test_validate_refuses_a_rejected_version(self):
        """驳回/隔离后不得再靠"提交校验"把自己救回来。"""
        version = self._candidate()
        version.release.state = "rejected"
        version.release.save(update_fields=["state"])

        with self.assertRaises(ValidationError) as ctx:
            SkillVersionService.submit_for_validation(version, actor=self.lead)
        self.assertIn("rejected", str(ctx.exception))

    def test_tampered_package_directory_is_detected_on_revalidation(self):
        """版本目录在入库后被改动过 → 重新校验必须发现，并转驳回。

        这条守的是"不可变"这三个字：目录名带内容哈希不等于内容不会变，
        必须每次校验都重算一遍。
        """
        version = self._candidate()
        package_dir = Path(version.get_full_path())
        (package_dir / "extra.md").write_text("被塞进来的文件\n", encoding="utf-8")

        with self.assertRaises(ValidationError) as ctx:
            SkillVersionService.submit_for_validation(version, actor=self.lead)

        version.refresh_from_db()
        self.assertEqual(version.state, "rejected")
        self.assertIn("哈希不一致", str(ctx.exception))

    def test_executor_can_submit_validation_through_the_api(self):
        """校验属于验评环节，执行人员即可发起（设计第 10 节）。"""
        version = self._candidate()
        response = self._client(self.executor).post(
            f"/api/projects/{self.project.pk}/skills/{version.skill_id}"
            f"/versions/{version.pk}/validate/",
            {"reason": "提交校验"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        # skills 域视图返回 {code, message, data}，平台包装层再包一层，故取两层 data。
        payload = response.json()["data"]["data"]
        self.assertEqual(payload["version"]["state"], "shadow")
        self.assertTrue(payload["report"]["ok"])

    def test_outsider_cannot_submit_validation(self):
        version = self._candidate()
        outsider = User.objects.create_user(username="t18-out-validate", password="pass")
        response = self._client(outsider).post(
            f"/api/projects/{self.project.pk}/skills/{version.skill_id}"
            f"/versions/{version.pk}/validate/", {}, format="json",
        )
        # R12 明确要求跨项目访问返回 403 且不泄露元数据，这里保持平台的 403 口径。
        self.assertEqual(response.status_code, 403, response.content)


class ReleaseApprovalViewTests(T18BaseTests):
    """`approval-view/`：控制台右栏的权威状态与"还差什么"。"""

    def test_missing_gate_is_reported_as_a_named_condition(self):
        """没跑门禁时不能只给一句"不允许"，要指名缺的是"评测门禁"。"""
        version = self._validated()
        release = version.release

        response = self._client(self.lead).get(f"{RELEASES_URL}{release.pk}/approval-view/")

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["data"]
        codes = [item["code"] for item in payload["missing_conditions"]]
        self.assertEqual(codes, ["gate_not_run"])
        self.assertTrue(payload["missing_conditions"][0]["detail"])
        self.assertFalse(payload["can_submit_approval"])
        self.assertFalse(payload["can_activate"])

    def test_each_failed_check_becomes_a_named_condition(self):
        """门禁跑过但不过 → 逐项列出，前端才有内容可渲染。"""
        version = self._validated()
        release = version.release
        _snapshot(release, passed=False, checks=[
            {"code": "sample_count", "label": "样本量", "ok": False,
             "detail": "候选样本 1 条，低于下限 3"},
            {"code": "no_l1_regression", "label": "L1 不回归", "ok": False,
             "detail": "L1 均值下降 0.20"},
            {"code": "baseline_comparable", "label": "基线可比", "ok": True, "detail": "可比"},
        ])

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{release.pk}/approval-view/"
        ).json()["data"]

        codes = [item["code"] for item in payload["missing_conditions"]]
        self.assertEqual(codes, ["sample_count", "no_l1_regression"])
        self.assertNotIn("baseline_comparable", codes)
        self.assertIn("低于下限", payload["missing_conditions"][0]["detail"])

    def test_gate_summary_is_exposed_so_the_panel_can_show_evidence(self):
        """结论不够，审批人要看逐项证据与阈值。"""
        version = self._validated()
        release = version.release
        _snapshot(release, passed=True, checks=[
            {"code": "sample_count", "label": "样本量", "ok": True, "detail": "候选样本 5 条"},
        ])

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{release.pk}/approval-view/"
        ).json()["data"]

        self.assertTrue(payload["gate"]["passed"])
        self.assertEqual(payload["gate"]["checks"][0]["code"], "sample_count")
        self.assertEqual(payload["missing_conditions"], [])

    def test_passed_gate_after_approval_submission_allows_activation(self):
        version = self._awaiting_approval()
        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{version.release.pk}/approval-view/"
        ).json()["data"]

        self.assertEqual(payload["missing_conditions"], [])
        self.assertTrue(payload["can_activate"])
        self.assertFalse(payload["evidence_stale"])
        self.assertEqual(payload["state"], "awaiting_approval")

    def test_re_evaluated_candidate_invalidates_the_earlier_approval(self):
        """审批后被重新评测 → 旧审批依据失效，不得再直接激活。

        没有这条，负责人签的是快照 A，上线的却是依据快照 B 的版本。
        """
        version = self._awaiting_approval()
        release = version.release
        EvaluationGateSnapshot.objects.filter(release=release, kind="full").update(
            content_hash="f" * 64,
        )

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{release.pk}/approval-view/"
        ).json()["data"]

        self.assertTrue(payload["evidence_stale"])
        self.assertFalse(payload["can_activate"])

    def test_executor_can_read_the_approval_view(self):
        """执行人员要看得到"为什么还不能提交"，否则不知道该补什么。"""
        version = self._validated()
        response = self._client(self.executor).get(
            f"{RELEASES_URL}{version.release.pk}/approval-view/"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_outsider_gets_404_without_metadata(self):
        version = self._validated()
        outsider = User.objects.create_user(username="t18-out-read", password="pass")
        response = self._client(outsider).get(
            f"{RELEASES_URL}{version.release.pk}/approval-view/"
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("missing_conditions", response.content.decode())


class SubmitApprovalAPITests(T18BaseTests):
    """`submit-approval/`：提交审批是验评动作，执行人员可发起。"""

    def test_submit_is_refused_when_the_gate_never_ran(self):
        """按钮前置条件与后端校验必须同源：接口说不能，服务层也真的不能。"""
        version = self._validated()
        response = self._client(self.lead).post(
            f"{RELEASES_URL}{version.release.pk}/submit-approval/",
            {"reason": "试试"}, format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("前置条件", response.content.decode())
        version.refresh_from_db()
        self.assertEqual(version.state, "shadow")

    def test_submit_moves_to_awaiting_approval_and_records_the_evidence_hash(self):
        version = self._validated()
        self.make_passing_gate(release=version.release)

        response = self._client(self.lead).post(
            f"{RELEASES_URL}{version.release.pk}/submit-approval/",
            {"reason": "门禁通过，请审批"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        version.refresh_from_db()
        self.assertEqual(version.state, "awaiting_approval")
        self.assertTrue(version.release.approval_snapshot_hash)
        self.assertTrue(version.release.gate_report.get("passed"))
        self.assertTrue(KnowledgeAuditLog.objects.filter(
            project=self.project, action="submit_approval",
        ).exists())

    def test_executor_can_submit_for_approval(self):
        version = self._validated()
        self.make_passing_gate(release=version.release)
        response = self._client(self.executor).post(
            f"{RELEASES_URL}{version.release.pk}/submit-approval/", {}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_repeated_submit_does_not_duplicate_the_approval_history(self):
        """前端连点两次不能产生两条审批历史。"""
        version = self._validated()
        self.make_passing_gate(release=version.release)
        client = self._client(self.lead)
        client.post(f"{RELEASES_URL}{version.release.pk}/submit-approval/", {}, format="json")
        version.refresh_from_db()
        first = version.release.decisions.count()

        client.post(f"{RELEASES_URL}{version.release.pk}/submit-approval/", {}, format="json")

        version.refresh_from_db()
        self.assertEqual(version.release.decisions.count(), first)


class RejectAndQuarantineAPITests(T18BaseTests):
    """驳回与隔离：仅测试负责人，且原因必填。"""

    def test_executor_cannot_reject(self):
        version = self._awaiting_approval()
        response = self._client(self.executor).post(
            f"{RELEASES_URL}{version.release.pk}/reject/",
            {"reason": "不合适"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_reject_requires_a_reason(self):
        version = self._awaiting_approval()
        response = self._client(self.lead).post(
            f"{RELEASES_URL}{version.release.pk}/reject/", {"reason": "   "}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_reject_moves_to_rejected_and_audits(self):
        version = self._awaiting_approval()
        response = self._client(self.lead).post(
            f"{RELEASES_URL}{version.release.pk}/reject/",
            {"reason": "指令改动无实质收益"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        version.refresh_from_db()
        self.assertEqual(version.state, "rejected")
        self.assertTrue(KnowledgeAuditLog.objects.filter(
            project=self.project, action="reject",
        ).exists())

    def test_executor_cannot_quarantine(self):
        version = self._awaiting_approval()
        response = self._client(self.executor).post(
            f"{RELEASES_URL}{version.release.pk}/quarantine/",
            {"reason": "疑似密钥泄露"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_quarantine_requires_a_reason(self):
        version = self._awaiting_approval()
        response = self._client(self.lead).post(
            f"{RELEASES_URL}{version.release.pk}/quarantine/", {}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_quarantined_release_can_no_longer_be_activated(self):
        """隔离是终态：从它出发不存在通往 active 的合法路径，且不得再被运行时加载。"""
        version = self._awaiting_approval()
        client = self._client(self.lead)
        client.post(
            f"{RELEASES_URL}{version.release.pk}/quarantine/",
            {"reason": "生产发现密钥痕迹"}, format="json",
        )
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        self.assertFalse(can_reach("quarantined", "active"))

        response = client.post(
            f"{RELEASES_URL}{version.release.pk}/promote/", {}, format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        self.assertFalse(version.is_runnable)

    def test_outsider_cannot_promote(self):
        version = self._awaiting_approval()
        outsider = User.objects.create_user(username="t18-out-promote", password="pass")
        response = self._client(outsider).post(
            f"{RELEASES_URL}{version.release.pk}/promote/", {}, format="json",
        )
        self.assertEqual(response.status_code, 404)


class ReleaseBindingsAPITests(T18BaseTests):
    """`bindings/`：这个 Skill 版本被谁在用（R10-5）。"""

    def _definition(self, *, name, stages, active_release=None, kind="skill",
                    project=None, mode="single"):
        return CapabilityDefinition.objects.create(
            project=project or self.project, kind=kind, name=name,
            evaluation_mode=mode, stages=list(stages),
            active_release=active_release,
        )

    def test_active_release_binding_is_marked_as_strong(self):
        version = self._validated()
        self._definition(
            name="用例审查能力", stages=["case_review"],
            active_release=version.release,
        )

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        ).json()["data"]

        self.assertEqual(payload["stage"], "case_review")
        self.assertEqual(len(payload["definitions"]), 1)
        self.assertEqual(payload["definitions"][0]["binding"], "active_release")

    def test_stage_candidate_is_marked_separately_with_its_position(self):
        """阶段命中但活跃版本是别人 → 是候选绑定，不是强绑定；并给出它在链路中的位置。"""
        version = self._validated(stage="testcase_generation")
        self._definition(
            name="全链路测试", stages=["test_plan_generation", "testcase_generation",
                                     "test_execution", "report_generation"],
            mode="workflow",
        )

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        ).json()["data"]

        self.assertEqual(len(payload["definitions"]), 1)
        item = payload["definitions"][0]
        self.assertEqual(item["binding"], "stage")
        self.assertEqual(item["position"], 2)
        self.assertEqual(item["stage_labels"][1], "测试用例生成")

    def test_bindings_are_confined_to_the_release_project(self):
        version = self._validated()
        self._definition(
            name="本项目能力", stages=["case_review"], active_release=version.release,
        )
        self._definition(
            name="他项目同阶段能力", stages=["case_review"], project=self.other_project,
        )

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        ).json()["data"]

        names = [item["name"] for item in payload["definitions"]]
        self.assertEqual(names, ["本项目能力"])

    def test_unknown_stage_does_not_over_bind(self):
        """阶段未知时只认强绑定，不能把所有能力定义都算成"被这个版本影响"。"""
        version = self._validated()
        version.release.config = {**version.release.config, "stage": ""}
        version.release.save(update_fields=["config"])
        version.manifest = {**version.manifest, "stage": ""}
        version.save(update_fields=["manifest"])
        self._definition(name="无关能力", stages=["test_execution"])

        payload = self._client(self.lead).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        ).json()["data"]

        self.assertEqual(payload["stage"], "")
        self.assertEqual(payload["definitions"], [])
        self.assertEqual(len(payload["workflow_stages"]), 4)

    def test_executor_can_read_bindings(self):
        version = self._validated()
        response = self._client(self.executor).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_outsider_gets_404(self):
        version = self._validated()
        outsider = User.objects.create_user(username="t18-out-bind", password="pass")
        response = self._client(outsider).get(
            f"{RELEASES_URL}{version.release.pk}/bindings/"
        )
        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ConsoleFlowTests(T18BaseTests):
    """控制台主链路：草稿 -> 校验 -> 门禁 -> 审批 -> 激活，一步都不能跳。"""

    def test_full_governance_chain_requires_every_step(self):
        version = self._candidate()
        client = self._client(self.lead)

        # 草稿状态：直接激活必须失败（未过门禁）。
        early = client.post(f"{RELEASES_URL}{version.release.pk}/promote/", {}, format="json")
        self.assertEqual(early.status_code, 400, early.content)

        # 未校验就提交审批：失败。
        skipped = client.post(
            f"{RELEASES_URL}{version.release.pk}/submit-approval/", {}, format="json",
        )
        self.assertEqual(skipped.status_code, 400, skipped.content)

        SkillVersionService.submit_for_validation(version, actor=self.lead)
        self.make_passing_gate(release=version.release)
        self.assertEqual(
            client.post(f"{RELEASES_URL}{version.release.pk}/submit-approval/",
                        {}, format="json").status_code,
            200,
        )
        promoted = client.post(f"{RELEASES_URL}{version.release.pk}/promote/",
                               {"reason": "评审通过"}, format="json")
        self.assertEqual(promoted.status_code, 200, promoted.content)

        version.refresh_from_db()
        self.assertEqual(version.state, "active")
        self.assertTrue(version.is_runnable)
        self.assertTrue(KnowledgeAuditLog.objects.filter(
            project=self.project, action="approve",
        ).exists())
