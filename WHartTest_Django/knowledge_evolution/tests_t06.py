"""T06：e 投票方案生成 Skill 升级为 L3。

这一组用例同时盯三件事，缺一件这次升级就是"名义上的 L3"：

- **包结构**：Skill 真的声明了等级、真的有 schema 与自检脚本，而不是只在文档里
  写了"我们支持 L3"；
- **自检脚本真的会拦错**：拿一份改坏的 `stage_result.json` 喂给它，必须退出码非 0
  并指出具体字段。脚本只会说"通过"等于没有脚本；
- **平台与 Skill 的口径一致**：同一份样例既要过 Skill 自己的收紧 schema，
  也要过平台的 `stage-result/v1` 校验器 —— 两边判据漂移时，Skill 在本地全绿、
  一上平台就变成"结构化协议失败"，那是最难查的一类故障。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from knowledge_evolution import stage_outputs as so

PACKAGE_NAME = "sse-evote-test-plan"


def _find_skill_package() -> Path | None:
    """定位 Skill 包。

    两个候选覆盖两种运行方式：本机开发（包在仓库根下的 ``staged-skills/``）与
    容器内（整个仓库以只读方式挂在 ``/workspace``）。找不到时由调用方 skip，
    而不是让用例失败——``staged-skills`` 不是 Django 应用的一部分，
    在只装了后端的机器上缺席是正常的。
    """
    base = Path(settings.BASE_DIR)
    candidates = [
        Path("/workspace/staged-skills") / PACKAGE_NAME,
        base.parent / "staged-skills" / PACKAGE_NAME,
        base.parent.parent / "staged-skills" / PACKAGE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


PACKAGE = _find_skill_package()


@unittest.skipIf(PACKAGE is None, "未找到 staged-skills 目录，跳过 Skill 包静态校验")
class SkillPackageStaticTests(SimpleTestCase):
    """包结构与等级声明。"""

    def test_required_files_exist(self):
        for relative in (
            "SKILL.md",
            "schemas/stage_result.json",
            "scripts/validate_stage_result.py",
            "examples/stage_result.valid.json",
            "examples/corpus.sample.json",
            "references/TestPlan.md",
        ):
            self.assertTrue((PACKAGE / relative).is_file(), f"缺少 {relative}")

    def test_manifest_declares_l3(self):
        from skills.validation import parse_manifest, validate_manifest

        manifest, parse_issues = parse_manifest(PACKAGE)
        self.assertEqual([issue for issue in parse_issues if issue.severity == "error"], [])

        self.assertEqual(so.read_declared_level(manifest), "L3")
        self.assertEqual(manifest.get("stage"), "test_plan_generation")
        # 声明的 schema 必须真的在包内，否则平台读声明等级时会指向一个不存在的文件。
        self.assertTrue((PACKAGE / manifest["stage_result_schema"]).is_file())

        errors = [issue for issue in validate_manifest(PACKAGE, manifest) if issue.severity == "error"]
        self.assertEqual(errors, [], errors)

    def test_skill_schema_is_stricter_than_the_platform_one(self):
        """Skill 的 schema 必须比平台更严，不能把平台的要求放松。"""
        skill_schema = json.loads((PACKAGE / "schemas/stage_result.json").read_text(encoding="utf-8"))
        platform_schema = so.load_schema()

        self.assertEqual(skill_schema["properties"]["stage"]["const"], "test_plan_generation")
        for key in platform_schema["required"]:
            self.assertIn(key, skill_schema["required"], f"Skill schema 放松了平台的必填字段 {key}")
        # L3 的三块拼图：需求关联、证据定位、决策摘要，Skill 侧必须比平台更明确。
        for key in ("requirements", "decision_summary"):
            self.assertIn(key, skill_schema["required"])

        item_rules = skill_schema["properties"]["items"]["items"]
        for key in ("requirement_ids", "evidence_ids", "scenario_type", "priority", "expected"):
            self.assertIn(key, item_rules["required"], f"Skill schema 未收紧 items.{key}")
        self.assertGreaterEqual(item_rules["properties"]["evidence_ids"]["minItems"], 1)

        evidence_rules = skill_schema["properties"]["evidence"]["items"]
        for key in ("document_id", "document_version", "chunk_id", "quote"):
            self.assertIn(key, evidence_rules["required"], f"Skill schema 未收紧 evidence.{key}")

        # 主产物必须含 test_plan.xlsx：否则声明里"有主产物"仍要靠文件名猜。
        self.assertIn("contains", skill_schema["properties"]["primary_artifacts"])

    def test_skill_md_documents_the_protocol_on_both_sides(self):
        """SKILL.md 与 references/TestPlan.md 都要写清"怎么产出信封"。"""
        skill_md = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
        test_plan = (PACKAGE / "references/TestPlan.md").read_text(encoding="utf-8")

        for text in (skill_md, test_plan):
            self.assertIn("stage_result.json", text)
            self.assertIn("validate_stage_result.py", text)
        # 稳定 ID 的规则写在 TestPlan 里，因为它是"怎么编号"的问题，不是协议问题。
        self.assertIn("TP-001", test_plan)
        self.assertIn("稳定", test_plan)


@unittest.skipIf(PACKAGE is None, "未找到 staged-skills 目录，跳过自检脚本验证")
class SelfCheckScriptTests(SimpleTestCase):
    """自检脚本必须能拦住改坏的产出。"""

    SCRIPT = PACKAGE / "scripts" / "validate_stage_result.py"

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="t06-selfcheck-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.work = Path(self._tmp)
        # 包目录是只读挂载，改动一律在临时目录里做。
        self.valid = json.loads((PACKAGE / "examples/stage_result.valid.json").read_text(encoding="utf-8"))
        self.corpus = PACKAGE / "examples" / "corpus.sample.json"

    def _run(self, payload: dict | None, *extra: str, raw: str | None = None):
        target = self.work / "stage_result.json"
        target.write_text(
            raw if raw is not None else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), str(target), *extra],
            capture_output=True, text=True,
        )

    def test_valid_sample_passes(self):
        result = self._run(self.valid, "--corpus", str(self.corpus))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[OK]", result.stdout)

    def test_duplicate_item_id_is_rejected(self):
        payload = json.loads(json.dumps(self.valid))
        payload["items"].append(dict(payload["items"][0]))
        result = self._run(payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("方案项 ID 重复", result.stdout)

    def test_unknown_evidence_reference_is_rejected(self):
        payload = json.loads(json.dumps(self.valid))
        payload["items"][0]["evidence_ids"] = ["EV-999"]
        result = self._run(payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("引用了不存在的证据：EV-999", result.stdout)
        self.assertIn("/items/0/evidence_ids/0", result.stdout)

    def test_missing_primary_artifact_is_rejected(self):
        payload = json.loads(json.dumps(self.valid))
        payload["primary_artifacts"] = [{"name": "方案.docx", "sha256": "0" * 64}]
        result = self._run(payload)
        self.assertEqual(result.returncode, 1)
        # 声明里必须含 test_plan.xlsx，否则前端又要靠文件名正则猜哪个是正主。
        self.assertIn("/primary_artifacts", result.stdout)

    def test_quote_not_found_in_chunk_is_rejected(self):
        payload = json.loads(json.dumps(self.valid))
        payload["evidence"][0]["quote"] = "这句原文里根本没有。"
        result = self._run(payload, "--corpus", str(self.corpus))
        self.assertEqual(result.returncode, 1)
        self.assertIn("原句未出现在", result.stdout)

    def test_content_hash_mismatch_is_rejected(self):
        payload = json.loads(json.dumps(self.valid))
        payload["evidence"][0]["content_hash"] = "sha256:" + "0" * 64
        result = self._run(payload, "--corpus", str(self.corpus))
        self.assertEqual(result.returncode, 1)
        self.assertIn("content_hash 与 quote 内容不一致", result.stdout)

    def test_artifact_hash_is_verified_against_disk(self):
        artifacts = self.work / "artifacts"
        artifacts.mkdir()
        plan = artifacts / "test_plan.xlsx"
        plan.write_bytes(b"plan-bytes")

        import hashlib

        payload = json.loads(json.dumps(self.valid))
        payload["primary_artifacts"][0]["sha256"] = hashlib.sha256(b"plan-bytes").hexdigest()
        result = self._run(payload, "--artifacts-dir", str(artifacts))
        self.assertEqual(result.returncode, 0, result.stdout)

        payload["primary_artifacts"][0]["sha256"] = "0" * 64
        result = self._run(payload, "--artifacts-dir", str(artifacts))
        self.assertEqual(result.returncode, 1)
        self.assertIn("主产物哈希不一致", result.stdout)

    def test_broken_json_is_reported_not_crashed(self):
        result = self._run(None, raw="{不是 JSON")
        self.assertEqual(result.returncode, 1)
        self.assertIn("不是合法 JSON", result.stdout)

    def test_unreferenced_requirement_is_a_warning_not_an_error(self):
        """需求被有意排期到下一轮是正常业务，不该把本次产出判为失败。"""
        payload = json.loads(json.dumps(self.valid))
        payload["requirements"].append({"id": "REQ-99", "title": "下一轮需求", "source": "待排期"})
        result = self._run(payload, "--corpus", str(self.corpus))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("覆盖缺口", result.stdout)


@unittest.skipIf(PACKAGE is None, "未找到 staged-skills 目录，跳过样例一致性校验")
class SampleAgreesWithPlatformTests(SimpleTestCase):
    """Skill 的样例必须同时过自家 schema 与平台校验器。"""

    def setUp(self):
        super().setUp()
        self.payload = json.loads(
            (PACKAGE / "examples/stage_result.valid.json").read_text(encoding="utf-8")
        )

    def test_platform_validator_accepts_the_sample_at_l3(self):
        result = so.validate_stage_result(
            self.payload,
            expected_stage="test_plan_generation",
            declared_level="L3",
        )
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.level, "L3")
        self.assertEqual(result.effective_level, "L3")
        self.assertFalse(result.level_gap)

    def test_sample_declares_the_primary_artifact_and_traceable_evidence(self):
        normalized = so.StageResult.from_payload(self.payload, "L3")
        self.assertIn("test_plan.xlsx", [item.get("name") for item in normalized.primary_artifacts])

        evidence_by_id = {entry["id"]: entry for entry in normalized.evidence}
        for item in normalized.items:
            self.assertTrue(item.get("requirement_ids"), item["id"])
            for evidence_id in item["evidence_ids"]:
                entry = evidence_by_id[evidence_id]
                # L3 的判据：定位三要素齐全，能回到具体文档版本的具体片段。
                for key in ("document_id", "document_version", "chunk_id", "quote"):
                    self.assertTrue(str(entry.get(key) or "").strip(), f"{evidence_id}.{key}")

    def test_sample_does_not_prefill_human_conclusion_fields(self):
        """人工结论字段必须留给确认报告，Skill 不得代填。"""
        text = json.dumps(self.payload, ensure_ascii=False)
        for forbidden in ("人工结论", "修改类型", "修改内容", "备注"):
            self.assertNotIn(forbidden, text)
