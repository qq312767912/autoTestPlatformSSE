"""T05 验收测试：Skill 包预检与安全扫描。

覆盖 tasks.md T05 的验收项——"恶意 ZIP、超限包、缺失 manifest、明文密钥和哈希
篡改均被自动化测试拦截；预检不写正式版本库"：

- 归档层：Zip Slip、绝对路径、符号链接、文件数/单文件/总体积/路径长度/层级/压缩比。
- 内容层：UTF-8、明文凭据（私钥/AWS/GitHub/OpenAI/Slack/Google/JWT/赋值/Bearer）、
  高熵可疑串、凭据文件、被禁二进制类型。
- 声明层：manifest 必填字段、版本格式、阶段合法性、入口与 Schema 可解析性。
- 令牌层：篡改拒绝、过期拒绝、预检后包被替换（TOCTOU）拒绝。
- 边界：脱敏占位符不得被当成密钥；预检不落任何正式数据。
"""
import io
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from projects.models import Project
from skills import validation
from skills.models import Skill, SkillVersion
from skills.validation import SkillPackageValidationService

#: 预检会把暂存包写进 MEDIA_ROOT，测试必须隔离出去，绝不能碰真实媒体目录。
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-preflight-")

SKILL_MD = """---
name: {name}
description: 用例审查能力
version: {version}
stage: {stage}
entrypoint: scripts/main.py
input_schema: schemas/input.json
output_schema: schemas/output.json
permissions: []
---
# 用例审查

按规则检查用例的可执行性。
"""


def build_zip(entries: dict, *, symlinks: dict | None = None) -> bytes:
    """把 ``{路径: 内容}`` 打成 zip；``symlinks`` 里的条目写成符号链接。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            # 文件类型位为 S_IFLNK，safe_extract_zip 据此拦下符号链接。
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


def valid_entries(**overrides) -> dict:
    """一份结构完整、可过检的最小技能包内容。"""
    return {
        "SKILL.md": SKILL_MD.format(
            name=overrides.get("name", "case-review"),
            version=overrides.get("version", "1.0.0"),
            stage=overrides.get("stage", "case_review"),
        ),
        "scripts/main.py": "print('review')\n",
        "schemas/input.json": json.dumps({"type": "object"}),
        "schemas/output.json": json.dumps({"type": "object"}),
    }


def upload(payload: bytes, name: str = "skill.zip") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, payload, content_type="application/zip")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class PreflightBaseTests(TestCase):
    """预检相关测试的公共夹具。"""

    def setUp(self):
        self.project = Project.objects.create(name="预检项目")
        self.user = User.objects.create_user(username="preflight-user", password="x")
        # 暂存区在同一个 MEDIA_ROOT 下跨测试共享，必须逐个清空：否则前一个用例
        # 留下的暂存包会让"未通过的包不留在暂存区"这类断言因为旧数据而失败。
        shutil.rmtree(validation.preflight_root(), ignore_errors=True)

    def preflight(self, payload: bytes, name="skill.zip"):
        return SkillPackageValidationService.preflight(zip_file=upload(payload, name), filename=name)

    def error_codes(self, result) -> set:
        return {issue.code for issue in result.report.errors}


class ValidPackageTests(PreflightBaseTests):
    """正常包必须通过，并且预检阶段不产生任何正式数据。"""

    def test_valid_package_passes_and_returns_token(self):
        result = self.preflight(build_zip(valid_entries()))
        self.assertTrue(result.report.ok, msg=[i.message for i in result.report.errors])
        self.assertTrue(result.token)
        self.assertEqual(result.report.manifest["name"], "case-review")
        self.assertEqual(len(result.report.package_sha256), 64)

    def test_preflight_writes_no_formal_records(self):
        """预检是纯暂存操作：不得落下 Skill / SkillVersion 任何一条记录。"""
        self.preflight(build_zip(valid_entries()))
        self.assertEqual(Skill.objects.count(), 0)
        self.assertEqual(SkillVersion.objects.count(), 0)

    def test_rejected_package_leaves_no_staging_dir(self):
        """未通过的包不留在暂存区，避免占盘和误消费。"""
        payload = build_zip({"SKILL.md": "no frontmatter"})
        result = self.preflight(payload)
        self.assertFalse(result.report.ok)
        root = validation.preflight_root()
        self.assertEqual(list(root.iterdir()), [])


class ArchiveSecurityTests(PreflightBaseTests):
    """归档层：路径穿越、链接、体积与层级限额。"""

    def test_zip_slip_is_rejected(self):
        entries = valid_entries()
        entries["../evil.py"] = "print('pwned')"
        result = self.preflight(build_zip(entries))
        self.assertFalse(result.report.ok)
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_absolute_path_is_rejected(self):
        entries = valid_entries()
        entries["/etc/passwd"] = "root:x:0:0"
        result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_windows_drive_path_is_rejected(self):
        entries = valid_entries()
        entries["C:/windows/system32/x.dll"] = b"\x00\x01"
        result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_symlink_is_rejected(self):
        result = self.preflight(build_zip(valid_entries(), symlinks={"escape": "../../etc/passwd"}))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_too_many_files_is_rejected(self):
        entries = valid_entries()
        entries.update({f"assets/f{i}.txt": "x" for i in range(10)})
        with mock.patch.object(validation, "MAX_FILES", 5):
            result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_oversized_single_file_is_rejected(self):
        entries = valid_entries()
        entries["assets/big.txt"] = "A" * 4096
        with mock.patch.object(validation, "MAX_SINGLE_FILE", 1024):
            result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_path_too_deep_is_rejected(self):
        entries = valid_entries()
        entries["a/b/c/d/e/f/g/h/i/j/k/deep.txt"] = "x"
        with mock.patch.object(validation, "MAX_PATH_DEPTH", 4):
            result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_path_too_long_is_rejected(self):
        entries = valid_entries()
        entries[f"assets/{'n' * 300}.txt"] = "x"
        result = self.preflight(build_zip(entries))
        self.assertIn("unsafe_archive", self.error_codes(result))

    def test_invalid_zip_is_rejected(self):
        result = self.preflight(b"this is not a zip file")
        self.assertIn("bad_zip", self.error_codes(result))


class ManifestValidationTests(PreflightBaseTests):
    """声明层：manifest 完整性与可解析性。"""

    def test_missing_skill_md_is_rejected(self):
        result = self.preflight(build_zip({"README.md": "# nothing"}))
        self.assertIn("missing_entry", self.error_codes(result))

    def test_missing_frontmatter_is_rejected(self):
        result = self.preflight(build_zip({"SKILL.md": "# 没有 frontmatter"}))
        self.assertIn("bad_frontmatter", self.error_codes(result))

    def test_missing_required_fields_is_rejected(self):
        skill_md = "---\ndescription: 只有描述没有名字\n---\n# x\n"
        result = self.preflight(build_zip({"SKILL.md": skill_md}))
        self.assertIn("manifest_incomplete", self.error_codes(result))

    def test_recommended_fields_are_warnings_only(self):
        """只有 name/description 的指令型包必须能过。

        平台上的存量 Skill 大多是纯指令型（没有脚本入口与 Schema），若把这些字段
        当硬必填，升级后所有历史包都会被自己的校验拒之门外。
        """
        skill_md = "---\nname: inline-only\ndescription: 只有内联指令\n---\n# x\n"
        result = self.preflight(build_zip({"SKILL.md": skill_md}))
        self.assertTrue(result.report.ok, msg=[i.message for i in result.report.errors])
        self.assertIn(
            "manifest_recommended_missing",
            {issue.code for issue in result.report.warnings},
        )

    def test_unknown_stage_is_rejected(self):
        result = self.preflight(build_zip(valid_entries(stage="not_a_stage")))
        self.assertIn("bad_stage", self.error_codes(result))

    def test_bad_version_format_is_rejected(self):
        result = self.preflight(build_zip(valid_entries(version="v1")))
        self.assertIn("bad_version", self.error_codes(result))

    def test_missing_entrypoint_is_rejected(self):
        entries = valid_entries()
        entries.pop("scripts/main.py")
        result = self.preflight(build_zip(entries))
        self.assertIn("bad_entrypoint", self.error_codes(result))

    def test_unparsable_schema_is_rejected(self):
        entries = valid_entries()
        entries["schemas/input.json"] = "{ not json"
        result = self.preflight(build_zip(entries))
        self.assertIn("bad_schema", self.error_codes(result))

    def test_permissions_must_be_list(self):
        entries = valid_entries()
        entries["SKILL.md"] = SKILL_MD.format(
            name="case-review", version="1.0.0", stage="case_review"
        ).replace("permissions: []", "permissions: everything")
        result = self.preflight(build_zip(entries))
        self.assertIn("bad_permissions", self.error_codes(result))


class SecretScanningTests(PreflightBaseTests):
    """内容层：明文凭据、高熵串与凭据文件。"""

    def _with_extra(self, relative, content):
        entries = valid_entries()
        entries[relative] = content
        return self.preflight(build_zip(entries))

    def test_private_key_is_rejected(self):
        result = self._with_extra("scripts/key.py", "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n")
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_aws_access_key_is_rejected(self):
        result = self._with_extra("scripts/aws.py", 'KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_github_token_is_rejected(self):
        result = self._with_extra("scripts/gh.py", 'token = "ghp_%s"\n' % ("a" * 36))
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_openai_key_is_rejected(self):
        result = self._with_extra("scripts/oai.py", 'k = "sk-%s"\n' % ("A" * 32))
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_jwt_is_rejected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        result = self._with_extra("scripts/jwt.py", f't = "{jwt}"\n')
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_assigned_password_is_rejected(self):
        result = self._with_extra("config/settings.ini", 'password = "SuperSecret12345"\n')
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_bearer_header_is_rejected(self):
        result = self._with_extra("scripts/api.md", "Authorization: Bearer " + "a" * 40 + "\n")
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_env_file_is_rejected(self):
        result = self._with_extra(".env", "FOO=bar\n")
        self.assertIn("credential_file", self.error_codes(result))

    def test_private_key_file_is_rejected(self):
        result = self._with_extra("certs/server.pem", "whatever")
        self.assertIn("credential_file", self.error_codes(result))

    def test_high_entropy_token_is_rejected(self):
        # 长度 >=32、大小写数字混杂、熵 >=4.5，且不是任何已知前缀格式。
        token = "Zx9Qm2Kp7Lr4Tv6Wn8Bd3Fg5Hj1Cs0Uy"
        result = self._with_extra("scripts/rand.py", f'token = "{token}"\n')
        self.assertTrue(any(code.startswith("secret:") for code in self.error_codes(result)))

    def test_lockfile_entropy_is_exempt(self):
        """哈希清单类文件天然高熵，不该被判成密钥。"""
        result = self._with_extra("package-lock.json", json.dumps({"hash": "A" * 64}))
        self.assertNotIn("secret:high_entropy", self.error_codes(result))

    def test_blocked_binary_is_rejected(self):
        entries = valid_entries()
        entries["bin/tool.exe"] = b"MZ\x90\x00"
        result = self.preflight(build_zip(entries))
        self.assertIn("blocked_binary", self.error_codes(result))

    def test_blocked_binary_allowed_when_declared(self):
        """manifest 显式声明 allow_executables 时降级为 warning。"""
        entries = valid_entries()
        entries["bin/tool.exe"] = b"MZ\x90\x00"
        entries["SKILL.md"] = SKILL_MD.format(
            name="case-review", version="1.0.0", stage="case_review"
        ).replace("permissions: []", "permissions: [allow_executables]")
        result = self.preflight(build_zip(entries))
        self.assertTrue(result.report.ok, msg=[i.message for i in result.report.errors])
        self.assertIn("blocked_binary", {i.code for i in result.report.warnings})

    def test_non_utf8_text_is_rejected(self):
        entries = valid_entries()
        entries["scripts/bad.py"] = b"\xff\xfe\x00invalid"
        result = self.preflight(build_zip(entries))
        self.assertIn("not_utf8", self.error_codes(result))


class PlaceholderTests(PreflightBaseTests):
    """脱敏占位符是"待填的配置"，不是泄漏的密钥。"""

    def test_redaction_placeholder_is_not_a_secret(self):
        entries = valid_entries()
        entries["scripts/main.py"] = (
            f'API_KEY = "{validation.API_KEY_PLACEHOLDER}"\n'
            f'KEY2 = os.environ.get("WHARTTEST_API_KEY", "{validation.API_KEY_PLACEHOLDER}")\n'
        )
        result = self.preflight(build_zip(entries))
        self.assertTrue(result.report.ok, msg=[i.message for i in result.report.errors])

    def test_scan_contents_exempts_placeholder(self):
        issues = validation.scan_contents({
            "scripts/main.py": f'API_KEY = "{validation.API_KEY_PLACEHOLDER}"'.encode(),
        })
        self.assertEqual(issues, [])


class PreflightTokenTests(PreflightBaseTests):
    """令牌层：消费令牌时的完整性约束。"""

    def test_consume_returns_staging_dir(self):
        result = self.preflight(build_zip(valid_entries()))
        payload, staging = SkillPackageValidationService.consume_preflight(token=result.token)
        self.assertEqual(payload["package_sha256"], result.report.package_sha256)
        self.assertTrue(staging.is_dir())

    def test_tampered_token_is_rejected(self):
        result = self.preflight(build_zip(valid_entries()))
        with self.assertRaises(Exception):
            SkillPackageValidationService.consume_preflight(token=result.token + "x")

    def test_expired_token_is_rejected(self):
        result = self.preflight(build_zip(valid_entries()))
        with mock.patch.object(validation, "PREFLIGHT_TTL_SECONDS", -1):
            with self.assertRaises(Exception):
                SkillPackageValidationService.consume_preflight(token=result.token)

    def test_staging_replacement_is_rejected(self):
        """预检通过后包被替换（TOCTOU）：哈希不一致必须拒绝并把暂存区删掉。"""
        result = self.preflight(build_zip(valid_entries()))
        staging = validation.preflight_root() / result.token_id
        (staging / "scripts" / "main.py").write_text("print('tampered')\n", encoding="utf-8")
        with self.assertRaises(Exception):
            SkillPackageValidationService.consume_preflight(token=result.token)
        self.assertFalse(staging.exists())

    def test_missing_staging_is_rejected(self):
        result = self.preflight(build_zip(valid_entries()))
        validation.cleanup_preflight(token_id=result.token_id)
        with self.assertRaises(Exception):
            SkillPackageValidationService.consume_preflight(token=result.token)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class PreflightMediaRootTests(TestCase):
    """验证暂存区严格落在 MEDIA_ROOT 下（不污染其它目录）。"""

    def test_preflight_root_under_media_root(self):
        root = validation.preflight_root()
        self.assertTrue(str(root).startswith(TEST_MEDIA_ROOT))
        self.assertTrue(Path(root).is_dir())
