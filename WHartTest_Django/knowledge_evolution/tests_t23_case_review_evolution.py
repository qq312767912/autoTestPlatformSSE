"""T23：用例审查「已确认报告 → Skill 候选版本」自进化入口。

每个用例只证明一件在真实业务里会出错的事：

- 报告里的人工结论有没有被**读对**（按列名读而不是按列号，列序一变就静默读错列）；
- 人工标「否」「是但改写」「留空」这三种情况有没有被分开（混在一起会把
  "AI 判断对了"也当成缺陷，护栏就会写进一条"别报这个"的错误约束）；
- 分数不达门槛时有没有**连归因都不落**（落了归因又不派生，库里会凭空多出
  一批没人认领的"已确认归因"，下一次派生会拿它们去改包）；
- 派生出来的候选是不是 draft、active 包有没有被碰、重复派生有没有被拦；
- 人工打分的落点是不是绑在了这次审查用的 Skill 版本上。
"""
from __future__ import annotations

import re
import tempfile
from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from knowledge_evolution.case_review_evolution import (
    CATEGORY_FALSE_ALARM,
    CATEGORY_REWRITTEN,
    DEFAULT_HUMAN_SCORE_THRESHOLD,
    CaseReviewEvolutionService,
    CaseReviewReportParser,
)
from knowledge_evolution.models import FeedbackEvent, GenerationOutput
from knowledge_evolution.skill_evolution import GUARDRAIL_BEGIN
from knowledge_evolution.task_binding import DEFAULT_CASE_REVIEW_SKILL_NAME
from knowledge_evolution.tests_t09_t13 import SkillHubBaseTests, TEST_MEDIA_ROOT
from knowledge_evolution.trace_models import FailureAttribution

#: 与 ``testcases/review_service.py::_write_report`` 的「问题明细」页列序逐字一致。
REPORT_HEADERS = [
    "Sheet", "行号", "用例编号/名称", "模块", "原文", "严重程度", "问题类型", "问题说明",
    "修改建议", "判定", "问题确认", "问题描述", "修改点", "不采纳原因",
]


def make_report(rows, *, headers=None, sheet_name="问题明细", extra_sheets=True) -> bytes:
    """造一份平台格式的审查报告。

    刻意用 openpyxl 真写一个 xlsx 而不是塞字典给解析器：这一层的风险恰恰在
    "真实文件里的表头、空行、下拉空值长什么样"，用内存对象测会把这类差异全绕过去。
    """
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers or REPORT_HEADERS)
    for row in rows:
        sheet.append([row.get(name, "") for name in (headers or REPORT_HEADERS)])
    if extra_sheets and sheet_name == "问题明细":
        workbook.create_sheet("审查摘要")
        workbook.create_sheet("测试确认处理结果")
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ReportParserTests(TestCase):
    """报告解析：人工结论的三种归宿必须分开。"""

    def test_affirmative_without_rewrite_is_not_a_defect(self):
        """人工标「是」且没改写 → AI 判断与表述都被认可，不进归因。

        这条如果漏了，护栏小节里会出现"别报这个问题"，而人工其实认可它——
        下一次执行会照着护栏把真问题漏掉。
        """
        data = make_report([
            {"问题类型": "步骤不明确", "问题确认": "是"},
            {"问题类型": "步骤不明确", "问题确认": "是"},
        ])

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(scan.affirmative, 2)
        self.assertEqual(scan.defects, ())
        self.assertEqual(scan.defect_total, 0)

    def test_negative_becomes_prompt_error_with_reason(self):
        data = make_report([
            {"问题类型": "措辞问题", "问题确认": "否", "不采纳原因": "原文已含等价表述",
             "Sheet": "用例", "行号": 12, "用例编号/名称": "TC-012"},
        ])

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(scan.negative, 1)
        self.assertEqual(len(scan.defects), 1)
        defect = scan.defects[0]
        self.assertEqual(defect.category, CATEGORY_FALSE_ALARM)
        self.assertEqual(defect.issue_type, "措辞问题")
        self.assertIn("原文已含等价表述", defect.hypothesis)
        # 归因指纹按 (review, category, issue_type) 算，证据里必须带得上原始位置，
        # 否则事后没人能回到那句话去核对。
        self.assertEqual(defect.samples[0]["row"], "12")
        self.assertEqual(defect.samples[0]["case_id"], "TC-012")

    def test_affirmative_with_rewrite_becomes_generation_error(self):
        data = make_report([
            {"问题类型": "预期结果模糊", "问题确认": "是", "问题描述": "人工重写的问题描述"},
        ])

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(scan.affirmative, 1)
        self.assertEqual(scan.rewritten, 1)
        self.assertEqual(scan.defects[0].category, CATEGORY_REWRITTEN)
        self.assertIn("人工重写的问题描述", scan.defects[0].hypothesis)

    def test_blank_confirmation_is_counted_not_silently_skipped(self):
        """留空必须计数并给警告：漏填会让"这份报告已全部确认"变成没人验证的假设。"""
        data = make_report([
            {"问题类型": "步骤不明确", "问题确认": "", "不采纳原因": "忘了填"},
            {"问题类型": "步骤不明确", "问题确认": "否"},
        ])

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(scan.unconfirmed, 1)
        self.assertTrue(any("未在「问题确认」列填写结论" in item for item in scan.warnings))
        self.assertEqual(scan.defect_total, 1)

    def test_columns_are_located_by_name_not_by_position(self):
        """列序调换后仍要读对——按列号读会在换版时静默把「修改点」当成「问题类型」。"""
        headers = list(REPORT_HEADERS)
        # 把「问题确认」与「修改点」互换位置，并整体反转列序。
        swapped = ["修改点" if name == "问题确认" else "问题确认" if name == "修改点" else name
                   for name in headers][::-1]
        data = make_report(
            [{"问题类型": "措辞问题", "问题确认": "否", "修改点": "这是修改点",
              "不采纳原因": "不成立"}],
            headers=swapped,
        )

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(scan.negative, 1)
        self.assertEqual(scan.defects[0].issue_type, "措辞问题")

    def test_missing_detail_sheet_is_refused(self):
        data = make_report([], sheet_name="审查摘要", extra_sheets=False)

        with self.assertRaises(ValidationError) as ctx:
            CaseReviewReportParser.parse(data)

        self.assertIn("问题明细", str(ctx.exception))

    def test_missing_required_column_is_refused(self):
        headers = [name for name in REPORT_HEADERS if name != "问题确认"]
        data = make_report([], headers=headers)

        with self.assertRaises(ValidationError) as ctx:
            CaseReviewReportParser.parse(data)

        self.assertIn("问题确认", str(ctx.exception))

    def test_empty_bytes_is_refused(self):
        with self.assertRaises(ValidationError):
            CaseReviewReportParser.parse(b"")

    def test_duplicate_defects_are_grouped_by_type(self):
        data = make_report([
            {"问题类型": "A", "问题确认": "否"},
            {"问题类型": "A", "问题确认": "否"},
            {"问题类型": "B", "问题确认": "否"},
        ])

        scan = CaseReviewReportParser.parse(data)

        self.assertEqual(len(scan.defects), 2)
        self.assertEqual(scan.defects[0].issue_type, "A")
        self.assertEqual(scan.defects[0].count, 2)
        self.assertEqual(scan.defect_total, 3)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class EvolutionFlowTests(SkillHubBaseTests):
    """从报告到候选版本的完整一步。"""

    def _activate(self, version):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")
        version.refresh_from_db()
        version.skill.refresh_from_db()
        return version

    def make_completed_review(self, *, skill_version=None, locked=True, source_name="用例.xlsx"):
        """造一条已跑完、且产出已发布到飞轮的用例审查。"""
        from testcases.models import TestCaseReview

        skill = skill_version.skill if skill_version else None
        review = TestCaseReview.objects.create(
            project=self.project, creator=self.lead,
            source_file=SimpleUploadedFile(source_name, b"placeholder"),
            source_name=source_name, status="completed",
            selected_skill=skill if locked else None,
            skill_name=DEFAULT_CASE_REVIEW_SKILL_NAME,
            summary={},
        )
        output = self.make_output(
            task_type="case_review", skill_version=skill_version,
            workflow_id=str(review.pk),
        )
        review.summary = {"output_id": str(output.id), "trace_id": str(output.trace_id)}
        review.save(update_fields=["summary"])
        return review, output

    def test_successful_evolution_returns_a_draft_candidate(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        baseline_sha = version.package_sha256
        data = make_report([
            {"问题类型": "措辞问题", "问题确认": "否", "不采纳原因": "原文已含等价表述",
             "行号": 3, "用例编号/名称": "TC-003"},
        ])

        result = CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=90, actor=self.lead,
            report_name="用例-已确认.xlsx",
        )

        self.assertEqual(result["baseline_version"], version.version)
        self.assertTrue(result["active_untouched"])
        self.assertEqual(result["candidate"]["state"], "draft")
        self.assertNotEqual(result["candidate"]["package_sha256"], baseline_sha)
        self.assertIn("/versions/", result["download_url"])
        # 尾斜杠不是风格问题：少一个就会 301，而前端是拿这个 URL 下 blob 的。
        self.assertTrue(result["download_url"].endswith("/download/"), result["download_url"])
        self.assertEqual(result["attribution_ids"], [
            str(FailureAttribution.objects.get(project=self.project).id)
        ])
        # 派生的实质产物必须能被人读懂，而不是只回一个版本号。
        self.assertTrue(result["diff"])
        self.assertEqual(result["rollback_target"], str(version.id))

    def test_attribution_signals_separate_false_alarm_from_rewrite(self):
        """两类缺陷的人工信号必须相反，而且要落在护栏里对应的那一条上。

        ``false_positive`` 读作"这条是误报，别再报"；``defect_confirmed`` 读作
        "这条成立，只是表述要改"。它们会被原样写进 SKILL.md 的护栏小节，交给
        下一次执行的模型当约束读——写反了就不是排版难看，而是**要求模型做相反的事**。
        """
        _skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([
            {"问题类型": "措辞冗余", "问题确认": "否", "不采纳原因": "原文已含等价表述", "行号": 1},
            {"问题类型": "预期结果不可验收", "问题确认": "是", "问题描述": "预期结果不可验收",
             "修改点": "改为：返回码 0000 且回报报文含 userId", "行号": 2},
        ])

        result = CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=90, actor=self.lead,
        )

        signals = {
            item.category: item.evidence[0]["feedback_signals"][0]
            for item in FailureAttribution.objects.filter(project=self.project)
        }
        self.assertEqual(signals, {
            CATEGORY_FALSE_ALARM: "false_positive",
            CATEGORY_REWRITTEN: "defect_confirmed",
        })

        # 信号最终是长在护栏条目上的，所以还要验它在包里的落位对不对。
        # 按 `N. [类别]` 切分而不是假定顺序：条目次序由报告行序决定，不是契约。
        from skills.models import SkillVersion

        candidate = SkillVersion.objects.get(pk=result["candidate"]["version_id"])
        skill_md = (Path(candidate.get_full_path()) / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(GUARDRAIL_BEGIN, skill_md, "候选包里必须出现护栏小节")
        guardrail = skill_md.split(GUARDRAIL_BEGIN)[1]
        items = {
            body.split("]", 1)[0]: body
            for body in re.split(r"\n\d+\. \[", guardrail)[1:]
        }
        self.assertIn("defect_confirmed", items[CATEGORY_REWRITTEN])
        self.assertNotIn("false_positive", items[CATEGORY_REWRITTEN])
        self.assertIn("false_positive", items[CATEGORY_FALSE_ALARM])

    def test_active_package_is_untouched_by_derivation(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        baseline_sha = version.package_sha256
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=88, actor=self.lead,
        )

        skill.refresh_from_db()
        self.assertEqual(str(skill.active_version_id), str(version.id))
        version.refresh_from_db()
        self.assertEqual(version.package_sha256, baseline_sha)

    def test_score_below_threshold_blocks_and_leaves_no_attribution(self):
        """未达门槛时**连归因都不该落**。

        落了归因又不派生，库里会凭空多出一批没人认领的"已确认归因"——
        下一次派生会拿它们去改包，而那次改进的依据是谁、为什么，已经查不到了。
        """
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        with self.assertRaises(ValidationError) as ctx:
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=59, actor=self.lead,
            )

        self.assertIn("未达到门槛", str(ctx.exception))
        self.assertFalse(FailureAttribution.objects.filter(project=self.project).exists())
        self.assertFalse(FeedbackEvent.objects.filter(project=self.project).exists())
        self.assertEqual(skill.versions.filter(source_type="evolution").count(), 0)

    def test_threshold_is_inclusive(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        result = CaseReviewEvolutionService.evolve(
            review=review, data=data, actor=self.lead,
            human_score=DEFAULT_HUMAN_SCORE_THRESHOLD,
        )

        self.assertEqual(result["human_score"], float(DEFAULT_HUMAN_SCORE_THRESHOLD))

    def test_report_without_confirmed_defect_is_refused(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "是"}])

        with self.assertRaises(ValidationError) as ctx:
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=95, actor=self.lead,
            )

        self.assertIn("没有可修复的缺陷", str(ctx.exception))

    def test_repeated_derivation_is_refused(self):
        """同一份确认报告派生第二次必须被拦——否则能靠重传刷出一堆同内容候选。"""
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=90, actor=self.lead,
        )
        with self.assertRaises(ValidationError):
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=90, actor=self.lead,
            )

        self.assertEqual(skill.versions.filter(source_type="evolution").count(), 1)
        # 归因也不能重复落：指纹里刻意不含文件哈希，重传只该复用同一条。
        self.assertEqual(FailureAttribution.objects.filter(project=self.project).count(), 1)

    def test_baseline_must_still_be_the_active_version(self):
        skill, first = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(first)
        review, _output = self.make_completed_review(skill_version=first)

        _skill, second = self.make_skill_version(
            name=DEFAULT_CASE_REVIEW_SKILL_NAME, version="9.9.9", body="新增护栏",
        )
        self._activate(second)

        data = make_report([{"问题类型": "A", "问题确认": "否"}])
        with self.assertRaises(ValidationError) as ctx:
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=95, actor=self.lead,
            )

        self.assertIn("已不是当前活跃版本", str(ctx.exception))

    def test_unfinished_review_is_refused(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        review.status = "running"
        review.save(update_fields=["status"])

        data = make_report([{"问题类型": "A", "问题确认": "否"}])
        with self.assertRaises(ValidationError) as ctx:
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=95, actor=self.lead,
            )

        self.assertIn("只有已完成的审查", str(ctx.exception))

    def test_unlocked_review_is_refused_with_actionable_message(self):
        review, _output = self.make_completed_review(skill_version=None)

        data = make_report([{"问题类型": "A", "问题确认": "否"}])
        with self.assertRaises(ValidationError) as ctx:
            CaseReviewEvolutionService.evolve(
                review=review, data=data, human_score=95, actor=self.lead,
            )

        self.assertIn("未锁定 Skill 版本", str(ctx.exception))

    def test_human_score_is_recorded_against_the_locked_version(self):
        """打分必须绑在**这次审查用的那个版本**上，否则事后无法回答"哪一版被人工否过"。"""
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        result = CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=72, actor=self.lead,
            report_name="报告.xlsx",
        )

        event = FeedbackEvent.objects.get(pk=result["feedback_id"])
        self.assertEqual(event.signal, "accepted")
        self.assertEqual(event.reason_code, "manual_calibration")
        self.assertAlmostEqual(event.value, 0.72, places=4)
        self.assertEqual(str(event.skill_version_id), str(version.pk))
        self.assertEqual(str(event.output_id), str(output.id))
        self.assertEqual(event.actor_id, self.lead.id)
        self.assertIn("72/100", event.comment)

    def test_failed_retry_leaves_no_orphan_score(self):
        """派生被拦时，归因与打分必须**整笔一起回滚**。

        留一条"人工打过分、但没有对应候选版本"的记录，事后没人能回答这次打分
        是给哪次进化用的——它会被当成一次成功的校准读进后续统计。
        """
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        review, _output = self.make_completed_review(skill_version=version)
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        first = CaseReviewEvolutionService.evolve(
            review=review, data=data, human_score=90, actor=self.lead, report_name="r.xlsx",
        )
        counted = FeedbackEvent.objects.filter(reason_code="manual_calibration")
        self.assertEqual(counted.count(), 1)

        # 同分重传与改分重传都会撞上"重复派生"，两次都不该多留打分。
        for score in (90, 80):
            with self.assertRaises(ValidationError):
                CaseReviewEvolutionService.evolve(
                    review=review, data=data, human_score=score, actor=self.lead,
                    report_name="r.xlsx",
                )
        self.assertEqual(counted.count(), 1)
        self.assertEqual(str(counted.first().pk), first["feedback_id"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class CandidateListingTests(SkillHubBaseTests):
    """列表里的"能不能进化"必须与 evolve 的真实判据一致。"""

    def _activate(self, version):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")
        version.refresh_from_db()
        version.skill.refresh_from_db()
        return version

    def _review(self, *, status="completed", skill_version=None, source_name="用例.xlsx"):
        from testcases.models import TestCaseReview

        review = TestCaseReview.objects.create(
            project=self.project, creator=self.lead,
            source_file=SimpleUploadedFile(source_name, b"placeholder"),
            source_name=source_name, status=status,
            skill_name=DEFAULT_CASE_REVIEW_SKILL_NAME, summary={},
        )
        if skill_version is not None:
            output = self.make_output(
                task_type="case_review", skill_version=skill_version,
                workflow_id=str(review.pk),
            )
            review.summary = {"output_id": str(output.id)}
            review.save(update_fields=["summary"])
        return review

    def test_only_completed_reviews_are_listed(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        done = self._review(skill_version=version, source_name="已完成.xlsx")
        self._review(status="running", skill_version=version, source_name="跑着的.xlsx")

        items = CaseReviewEvolutionService.list_candidates(project=self.project)

        self.assertEqual([item["review_id"] for item in items], [str(done.pk)])

    def test_evolvable_flags_follow_the_real_preconditions(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        ready = self._review(skill_version=version, source_name="可用.xlsx")
        unlocked = self._review(source_name="没锁版本.xlsx")

        items = {item["review_id"]: item for item in CaseReviewEvolutionService.list_candidates(
            project=self.project
        )}

        self.assertTrue(items[str(ready.pk)]["evolvable"])
        self.assertEqual(items[str(ready.pk)]["blockers"], [])
        self.assertTrue(items[str(ready.pk)]["is_active_version"])
        self.assertEqual(items[str(ready.pk)]["skill_version"], version.version)

        self.assertFalse(items[str(unlocked.pk)]["evolvable"])
        self.assertTrue(items[str(unlocked.pk)]["blockers"])

    def test_other_projects_reviews_are_not_listed(self):
        skill, version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(version)
        self._review(skill_version=version)

        items = CaseReviewEvolutionService.list_candidates(project=self.other_project)

        self.assertEqual(items, [])

    def test_stale_baseline_is_reported_as_a_blocker(self):
        skill, first = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(first)
        review = self._review(skill_version=first)
        _skill, second = self.make_skill_version(
            name=DEFAULT_CASE_REVIEW_SKILL_NAME, version="9.9.9", body="新增护栏",
        )
        self._activate(second)

        items = CaseReviewEvolutionService.list_candidates(project=self.project)

        item = next(row for row in items if row["review_id"] == str(review.pk))
        self.assertFalse(item["evolvable"])
        self.assertTrue(any("活跃版本" in text for text in item["blockers"]))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class EvolutionApiTests(SkillHubBaseTests):
    """接口层：权限、参数与错误码。"""

    def _activate(self, version):
        from knowledge_evolution.capabilities import CapabilityReleaseService

        version.release.gate_report = {"passed": True}
        version.release.state = "awaiting_approval"
        version.release.save(update_fields=["gate_report", "state"])
        CapabilityReleaseService.promote(version.release, actor=self.lead, reason="激活")
        version.refresh_from_db()
        version.skill.refresh_from_db()
        return version

    def _review(self, skill_version):
        from testcases.models import TestCaseReview

        review = TestCaseReview.objects.create(
            project=self.project, creator=self.lead,
            source_file=SimpleUploadedFile("用例.xlsx", b"placeholder"),
            source_name="用例.xlsx", status="completed",
            skill_name=DEFAULT_CASE_REVIEW_SKILL_NAME, summary={},
        )
        output = self.make_output(
            task_type="case_review", skill_version=skill_version,
            workflow_id=str(review.pk),
        )
        review.summary = {"output_id": str(output.id)}
        review.save(update_fields=["summary"])
        return review

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(user=self.lead)
        self.skill, self.version = self.make_skill_version(name=DEFAULT_CASE_REVIEW_SKILL_NAME)
        self._activate(self.version)
        self.review = self._review(self.version)

    def _upload(self, data: bytes, name="报告.xlsx"):
        return SimpleUploadedFile(
            name, data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_candidates_endpoint_returns_listing_and_threshold(self):
        response = self.client.get(
            "/api/knowledge-evolution/case-review-evolution/candidates/",
            {"project": self.project.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["threshold"], DEFAULT_HUMAN_SCORE_THRESHOLD)
        self.assertEqual(len(response.data["items"]), 1)

    def test_non_member_is_denied(self):
        from rest_framework.test import APIClient

        outsider = APIClient()
        outsider.force_authenticate(user=self.executor)
        response = outsider.get(
            "/api/knowledge-evolution/case-review-evolution/candidates/",
            {"project": self.other_project.id},
        )

        self.assertEqual(response.status_code, 403)

    def test_preflight_reports_every_blocker_without_writing_anything(self):
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        response = self.client.post(
            "/api/knowledge-evolution/case-review-evolution/preflight/",
            {"project": self.project.id, "review_id": str(self.review.pk),
             "human_score": "40", "file": self._upload(data)},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ready"])
        self.assertTrue(any("低于门槛" in text for text in response.data["blockers"]))
        self.assertEqual(response.data["scan"]["negative"], 1)
        # 预检不落库：归因、反馈、候选一个都不该出现。
        self.assertFalse(FailureAttribution.objects.filter(project=self.project).exists())
        self.assertFalse(FeedbackEvent.objects.filter(project=self.project).exists())

    def test_preflight_marks_ready_when_all_conditions_hold(self):
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        response = self.client.post(
            "/api/knowledge-evolution/case-review-evolution/preflight/",
            {"project": self.project.id, "review_id": str(self.review.pk),
             "human_score": "90", "file": self._upload(data)},
            format="multipart",
        )

        self.assertTrue(response.data["ready"])
        self.assertEqual(response.data["blockers"], [])

    def test_evolve_endpoint_returns_candidate_and_download_url(self):
        data = make_report([{"问题类型": "措辞问题", "问题确认": "否"}])

        response = self.client.post(
            "/api/knowledge-evolution/case-review-evolution/evolve/",
            {"project": self.project.id, "review_id": str(self.review.pk),
             "human_score": "90", "file": self._upload(data)},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["candidate"]["state"], "draft")
        self.assertIn(f"/api/projects/{self.project.id}/skills/{self.skill.id}/", response.data["download_url"])

    def test_business_refusal_is_400_not_500(self):
        """业务拒绝必须是 400。回 500 会被前端当"系统错误"弹掉，把该看的提示吃掉。"""
        data = make_report([{"问题类型": "A", "问题确认": "否"}])

        response = self.client.post(
            "/api/knowledge-evolution/case-review-evolution/evolve/",
            {"project": self.project.id, "review_id": str(self.review.pk),
             "human_score": "10", "file": self._upload(data)},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("门槛", response.data["detail"])

    def test_missing_file_is_rejected_as_bad_request(self):
        response = self.client.post(
            "/api/knowledge-evolution/case-review-evolution/evolve/",
            {"project": self.project.id, "review_id": str(self.review.pk), "human_score": "90"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)

    def test_foreign_project_review_is_not_reachable(self):
        from rest_framework.test import APIClient

        outsider = APIClient()
        outsider.force_authenticate(user=self.lead)
        response = outsider.post(
            "/api/knowledge-evolution/case-review-evolution/evolve/",
            {"project": self.other_project.id, "review_id": str(self.review.pk),
             "human_score": "90", "file": self._upload(make_report([]))},
            format="multipart",
        )

        self.assertEqual(response.status_code, 404)
