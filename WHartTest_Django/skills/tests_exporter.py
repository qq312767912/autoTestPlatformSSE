"""T07 验收测试：确定性脱敏下载与隔离。

覆盖 tasks.md T07 的验收项——"相同版本重复导出字节和 SHA-256 一致；重新上传可恢复
相同定义；凭据、缓存、日志和运行产物不进入包"，以及实现要点里的稳定排序与时间戳、
流式响应、下载前二次敏感扫描与审计。

"历史遗留包"用 ``_legacy_version`` 直接落盘构造：正常入库路径会拦下含凭据的包，
但存量迁移过来的包没有经过扫描，导出时必须由第二道扫描兜住。
"""
import hashlib
import io
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from knowledge_evolution.capability_models import CapabilityRelease, PromotionDecision
from knowledge_evolution.knowledge_models import KnowledgeAuditLog
from projects.models import Project
from skills.exporter import ZIP_FIXED_TIMESTAMP, SkillPackageExporter
from skills.models import Skill, SkillVersion
from skills.packaging import API_KEY_PLACEHOLDER
from skills.validation import safe_extract_zip
from skills.versions import SkillVersionService, version_storage_path

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="wharttest-exporter-")

SKILL_MD = """---
name: {name}
description: 用例审查能力
version: {version}
stage: case_review
---
# 用例审查

{body}
"""


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ExporterTestBase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="导出项目")
        self.user = User.objects.create_user(username="export-user", password="x")
        self.tmp = tempfile.mkdtemp(prefix="pkg-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ 夹具

    def make_package(self, *, name="case-review", version="1.0.0", body="规则 v1",
                     script=None, extra=None) -> Path:
        root = Path(self.tmp) / f"{name}-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(
            SKILL_MD.format(name=name, version=version, body=body), encoding="utf-8",
        )
        if script is not None:
            (root / "scripts").mkdir(exist_ok=True)
            (root / "scripts" / "main.py").write_text(script, encoding="utf-8")
        for relative, payload in (extra or {}).items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        return root

    def create(self, source, **kwargs):
        return SkillVersionService.create_candidate_from_dir(
            source_dir=source, project=self.project, actor=self.user, **kwargs,
        )

    def legacy_version(self, files: dict, *, version="0.0.0-migrated", state="draft"):
        """绕过入库校验直接造一个版本目录，模拟存量迁移过来的历史包。"""
        skill = Skill.objects.create(
            project=self.project, name=f"legacy-{uuid.uuid4().hex[:6]}", description="历史包",
        )
        sha = "0" * 64
        package_path = version_storage_path(
            project_id=self.project.id, skill_id=skill.id, version=version, package_sha256=sha,
        )
        root = Path(TEST_MEDIA_ROOT) / package_path
        root.mkdir(parents=True, exist_ok=True)
        for relative, payload in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")

        release = CapabilityRelease.objects.create(
            project=self.project, kind="skill", name=skill.name, version=version,
            artifact_hash=sha, state=state,
        )
        return SkillVersion.objects.create(
            skill=skill, version=version, release=release,
            package_path=package_path, package_sha256=sha,
            manifest={"name": skill.name, "version": version},
            source_type="migration",
        )

    @staticmethod
    def read_zip(payload: bytes) -> dict:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return {info.filename: archive.read(info.filename) for info in archive.infolist()}


class DeterminismTests(ExporterTestBase):
    """确定性：同一版本重复导出必须字节一致。"""

    def test_repeated_export_is_byte_identical(self):
        _skill, version = self.create(self.make_package())
        first = SkillPackageExporter.export_bytes(version, actor=self.user)
        second = SkillPackageExporter.export_bytes(version, actor=self.user)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(first.export_sha256, second.export_sha256)
        self.assertEqual(
            hashlib.sha256(first.payload).hexdigest(), first.export_sha256,
        )

    def test_entries_are_sorted_and_timestamped(self):
        _skill, version = self.create(self.make_package(extra={"docs/zzz.md": "z\n", "aaa.md": "a\n"}))
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        with zipfile.ZipFile(io.BytesIO(result.payload)) as archive:
            infos = archive.infolist()
        names = [info.filename for info in infos]
        self.assertEqual(names, sorted(names, key=lambda item: item.encode("utf-8")))
        # 固定时间戳：否则两次导出会因为"当前时间"不同而产生不同字节。
        self.assertTrue(all(info.date_time == ZIP_FIXED_TIMESTAMP for info in infos))

    def test_no_extra_or_comment_metadata(self):
        _skill, version = self.create(self.make_package())
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        with zipfile.ZipFile(io.BytesIO(result.payload)) as archive:
            self.assertEqual(archive.comment, b"")
            for info in archive.infolist():
                self.assertEqual(info.extra, b"")
                self.assertEqual(info.comment, b"")

    def test_script_entries_keep_exec_bit(self):
        _skill, version = self.create(self.make_package(script="print('ok')\n"))
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        with zipfile.ZipFile(io.BytesIO(result.payload)) as archive:
            mode = archive.getinfo("scripts/main.py").external_attr >> 16
            skill_md_mode = archive.getinfo("SKILL.md").external_attr >> 16
        self.assertTrue(mode & 0o111, "脚本应保留可执行位")
        self.assertFalse(skill_md_mode & 0o111, "普通文件不应有可执行位")


class ContentExclusionTests(ExporterTestBase):
    """凭据、缓存、日志、运行产物不得进入导出包。"""

    def test_runtime_artifacts_are_excluded(self):
        version = self.legacy_version({
            "SKILL.md": SKILL_MD.format(name="legacy", version="1.0.0", body="x"),
            ".git/config": "[core]\n",
            "__pycache__/mod.pyc": "junk",
            "logs/run.log": "2026-10-01 INFO x\n",
            "state/cache.db": "sqlite",
            ".env": "SECRET=1\n",
            "certs/key.pem": "-----BEGIN PRIVATE KEY-----\n",
            "node_modules/pkg/index.js": "module.exports = 1\n",
            "build/output.txt": "artifact\n",
            "report.tmp": "tmp\n",
        })
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        names = set(self.read_zip(result.payload))
        self.assertEqual(names, {"SKILL.md"})

    def test_no_credentials_in_export(self):
        """历史包里的凭据文件不会跟着出去。"""
        version = self.legacy_version({
            "SKILL.md": SKILL_MD.format(name="legacy", version="1.0.0", body="x"),
            "id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n",
            ".netrc": "machine x login y password z\n",
        })
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        self.assertNotIn("id_rsa", self.read_zip(result.payload))
        self.assertNotIn(".netrc", self.read_zip(result.payload))


class RedactionTests(ExporterTestBase):
    """反向脱敏：注入的 API Key 必须还原成占位符。"""

    def test_injected_api_key_is_redacted(self):
        _skill, version = self.create(
            self.make_package(name="whart-test", script='API_KEY = "placeholder"\n'),
            api_key="platform-real-key-123",
        )
        stored = (Path(version.get_full_path()) / "scripts" / "main.py").read_text(encoding="utf-8")
        self.assertIn("platform-real-key-123", stored, "前置条件：真实 Key 确实被注入了落盘包")

        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        exported = self.read_zip(result.payload)["scripts/main.py"].decode("utf-8")
        self.assertNotIn("platform-real-key-123", exported)
        self.assertIn(API_KEY_PLACEHOLDER, exported)
        self.assertEqual(result.redacted_files, ["scripts/main.py"])

    def test_env_get_form_is_redacted(self):
        _skill, version = self.create(
            self.make_package(
                name="api-automation",
                script='KEY = os.environ.get("WHARTTEST_API_KEY", "placeholder")\n',
            ),
            api_key="another-real-key-456",
        )
        exported = self.read_zip(
            SkillPackageExporter.export_bytes(version, actor=self.user).payload
        )["scripts/main.py"].decode("utf-8")
        self.assertNotIn("another-real-key-456", exported)
        self.assertIn(API_KEY_PLACEHOLDER, exported)

    def test_plain_package_is_not_touched(self):
        _skill, version = self.create(self.make_package(script="print('nothing secret')\n"))
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        self.assertEqual(result.redacted_files, [])

    def test_redaction_is_idempotent(self):
        """已经含占位符的内容再脱敏一次必须字节不变，否则重复导出就不确定了。"""
        _skill, version = self.create(
            self.make_package(script=f'API_KEY = "{API_KEY_PLACEHOLDER}"\n'),
        )
        result = SkillPackageExporter.export_bytes(version, actor=self.user)
        self.assertEqual(result.redacted_files, [])
        self.assertIn(
            API_KEY_PLACEHOLDER,
            self.read_zip(result.payload)["scripts/main.py"].decode("utf-8"),
        )


class ReimportTests(ExporterTestBase):
    """T07 关键验收：导出的包重新上传能恢复相同定义。"""

    def test_reexport_reupload_hits_same_version(self):
        _skill, version = self.create(self.make_package(version="1.0.0", body="规则 v1"))
        payload = SkillPackageExporter.export_bytes(version, actor=self.user).payload

        dest = Path(self.tmp) / f"reupload-{uuid.uuid4().hex[:6]}"
        dest.mkdir()
        safe_extract_zip(io.BytesIO(payload), str(dest))

        _skill2, reimported = SkillVersionService.create_candidate_from_dir(
            source_dir=dest, project=self.project, actor=self.user,
        )
        # 幂等命中：没有生成"看起来一样"的重复版本。
        self.assertEqual(reimported.id, version.id)
        self.assertEqual(SkillVersion.objects.filter(skill=version.skill).count(), 1)
        self.assertEqual(reimported.manifest["name"], version.manifest["name"])

    def test_redacted_package_reupload_hits_same_version(self):
        """含注入 Key 的包：导出脱敏后重传，仍须命中同一版本。"""
        _skill, version = self.create(
            self.make_package(name="whart-test", version="1.0.0", script='API_KEY = "seed"\n'),
            api_key="platform-real-key-999",
        )
        payload = SkillPackageExporter.export_bytes(version, actor=self.user).payload

        dest = Path(self.tmp) / f"reupload-{uuid.uuid4().hex[:6]}"
        dest.mkdir()
        safe_extract_zip(io.BytesIO(payload), str(dest))

        # 重新上传时用户重新提供 Key（占位符会被平台注入流程覆盖）。
        _skill2, reimported = SkillVersionService.create_candidate_from_dir(
            source_dir=dest, project=self.project, actor=self.user,
            api_key="platform-real-key-999",
        )
        self.assertEqual(reimported.id, version.id)

    def test_exported_package_passes_own_validator(self):
        """自洽性：自己导出的包必须能过自己的预检，不能被脱敏结果拦下。"""
        _skill, version = self.create(
            self.make_package(name="whart-test", script='API_KEY = "seed"\n'),
            api_key="platform-real-key-777",
        )
        payload = SkillPackageExporter.export_bytes(version, actor=self.user).payload

        from skills.validation import SkillPackageValidationService
        from django.core.files.uploadedfile import SimpleUploadedFile

        result = SkillPackageValidationService.preflight(
            zip_file=SimpleUploadedFile("x.zip", payload), filename="x.zip",
        )
        self.assertTrue(result.report.ok, msg=[i.message for i in result.report.errors])


class LeakQuarantineTests(ExporterTestBase):
    """导出前二次扫描命中凭据：拒绝下载并转隔离。"""

    def test_credential_leak_blocks_download_and_quarantines(self):
        version = self.legacy_version({
            "SKILL.md": SKILL_MD.format(name="legacy", version="1.0.0", body="x"),
            "scripts/leak.py": f'API_KEY = "sk-{"A" * 32}"\n',
        })
        with self.assertRaises(ValidationError):
            SkillPackageExporter.export_bytes(version, actor=self.user)

        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        self.assertTrue(KnowledgeAuditLog.objects.filter(
            entity_id=str(version.id), action="quarantine",
        ).exists())

    def test_high_entropy_leak_blocks_download(self):
        version = self.legacy_version({
            "SKILL.md": SKILL_MD.format(name="legacy", version="1.0.0", body="x"),
            "scripts/leak.py": 'T = "Zx9Qm2Kp7Lr4Tv6Wn8Bd3Fg5Hj1Cs0Uy"\n',
        })
        with self.assertRaises(ValidationError):
            SkillPackageExporter.export_bytes(version, actor=self.user)
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")

    def test_already_quarantined_version_is_not_reflowed(self):
        """已隔离的版本再次导出：仍拒绝，但不重复流转状态、不重复写裁决记录。"""
        version = self.legacy_version({
            "SKILL.md": SKILL_MD.format(name="legacy", version="1.0.0", body="x"),
            "scripts/leak.py": f'API_KEY = "sk-{"B" * 32}"\n',
        })
        with self.assertRaises(ValidationError):
            SkillPackageExporter.export_bytes(version, actor=self.user)
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        decisions_after_first = PromotionDecision.objects.filter(
            release_id=version.release_id,
        ).count()

        with self.assertRaises(ValidationError):
            SkillPackageExporter.export_bytes(version, actor=self.user)
        version.refresh_from_db()
        self.assertEqual(version.state, "quarantined")
        self.assertEqual(
            PromotionDecision.objects.filter(release_id=version.release_id).count(),
            decisions_after_first,
        )


class ResponseAndAuditTests(ExporterTestBase):
    """流式响应、响应头与审计。"""

    def test_response_headers_expose_both_hashes(self):
        _skill, version = self.create(self.make_package())
        response = SkillPackageExporter.export_response(version, actor=self.user)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["X-Package-Sha256"], version.package_sha256)
        self.assertEqual(response["X-Skill-Version-Id"], str(version.id))
        self.assertEqual(len(response["X-Export-Sha256"]), 64)

        body = b"".join(response.streaming_content)
        self.assertEqual(hashlib.sha256(body).hexdigest(), response["X-Export-Sha256"])
        self.assertEqual(response["Content-Length"], str(len(body)))

    def test_download_writes_audit(self):
        _skill, version = self.create(self.make_package())
        SkillPackageExporter.export_bytes(version, actor=self.user)
        log = KnowledgeAuditLog.objects.filter(
            entity_id=str(version.id), action="download",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.detail["package_sha256"], version.package_sha256)

    def test_export_filename_is_sanitized(self):
        _skill, version = self.create(self.make_package(name="case-review"))
        self.assertEqual(SkillPackageExporter.export_filename(version), "case-review-1.0.0.zip")

    def test_missing_package_dir_raises(self):
        _skill, version = self.create(self.make_package())
        shutil.rmtree(version.get_full_path(), ignore_errors=True)
        with self.assertRaises(ValidationError):
            SkillPackageExporter.export_bytes(version, actor=self.user)

    def test_export_manifest_lists_files_without_content(self):
        _skill, version = self.create(self.make_package(script="print(1)\n"))
        manifest = SkillPackageExporter.export_manifest(version)
        self.assertTrue(manifest["available"])
        self.assertIn("SKILL.md", manifest["files"])
        self.assertIn("scripts/main.py", manifest["files"])

    def test_export_manifest_reports_unavailable(self):
        _skill, version = self.create(self.make_package())
        shutil.rmtree(version.get_full_path(), ignore_errors=True)
        manifest = SkillPackageExporter.export_manifest(version)
        self.assertFalse(manifest["available"])
        self.assertEqual(manifest["files"], [])
