"""Skill 包预检与安全扫描（T05 / R1）。

两阶段设计：

1. **提交前预检** ``preflight()``：把 ZIP 安全解压到**暂存区**（不写正式版本库），
   返回 manifest、文件清单、包哈希和问题清单，并签一个带过期时间的令牌。
2. **保存候选** ``consume_preflight()``：只接受已通过预检、令牌未过期且当前包哈希
   与预检时一致的暂存包，然后原子搬进不可变存储目录。

安全策略集中在 ``scan_package()``：Zip Slip、符号链接/硬链接、文件数/单文件体积/
总体积、超长路径与目录深度、UTF-8 可读性、禁止的二进制可执行类型、明文凭据与高熵
可疑串、manifest 必填字段、JSON Schema 可解析性、入口存在性与阶段合法性。

**不会**误伤平台自身的内部 Skill：API Key 注入发生在"落盘之后"（见
``skills.platform_skills``），扫描对象始终是用户上传的原始包。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from .packaging import (
    API_KEY_PLACEHOLDER,
    PLACEHOLDER_LITERAL,
    compute_package_sha256,
    iter_package_files,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 限额
MAX_FILES = 2000
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024
MAX_SINGLE_FILE = 20 * 1024 * 1024
MAX_PATH_LENGTH = 200
MAX_PATH_DEPTH = 12
MAX_COMPRESSION_RATIO = 200  # zip bomb 启发式：解压/压缩比上限

# 预检令牌有效期（秒）与暂存区目录名。
PREFLIGHT_TTL_SECONDS = 30 * 60
PREFLIGHT_SALT = "skills.preflight.v1"
PREFLIGHT_ROOT_NAME = ".skill-preflight"

# manifest 字段分级（设计 3.1 + 存量兼容约束）。
#
# 硬必填：缺失即**拒绝入库**。只有这两个字段是任何历史包都具备的。
REQUIRED_MANIFEST_FIELDS = ("name", "description")

# 建议声明：缺失只记 warning。
#
# 为什么不做硬必填：平台上绝大多数 Skill 是"指令型"——包里只有一份描述规则的
# SKILL.md，没有脚本入口、没有 JSON Schema、没有权限声明。若把它们当硬必填，
# 平台升级之后所有历史包都会被自己的校验拒之门外，"重新上传恢复相同定义"这条
# 验收就直接失效了。声明的字段仍然逐个校验合法性（有则必须合法）。
RECOMMENDED_MANIFEST_FIELDS = (
    "version", "stage", "entrypoint", "input_schema", "output_schema", "permissions",
)

#: 允许的能力阶段，与知识飞轮统一产出协议保持一致。
#: ⚠️ 这是**只读副本**：真值在 ``knowledge_evolution/capability_registry.ALL_TASK_TYPES``。
#: 本模块不能反向 import 它（``knowledge_evolution`` 依赖 ``skills.models``，会成环），
#: 所以改成用测试守一致：``tests_t22.test_skills_manifest_valid_stages_match_the_registry``。
#: 副本过期时的症状很难查——写进 manifest 的合法阶段会被拒，报错只说"未知阶段"。
VALID_STAGES = frozenset({
    "case_review", "code_review", "knowledge_query", "risk_identification",
    "test_plan_generation", "testcase_generation", "test_execution",
    "issue_tracking", "report_generation",
})

#: 默认拒绝的二进制可执行类型（需在 manifest.permissions 里显式声明才放行）。
BLOCKED_BINARY_SUFFIXES = (
    ".exe", ".dll", ".msi", ".com", ".scr", ".sys", ".drv",
    ".so", ".dylib", ".dylib.*", ".ko", ".bin", ".jar", ".apk", ".dmg", ".iso",
)

#: 需要做内容扫描的文本文件后缀（其余二进制文件跳过，避免误报与性能损耗）。
TEXT_SUFFIXES = (
    ".md", ".txt", ".py", ".js", ".ts", ".mjs", ".cjs", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".sh", ".bash", ".zsh", ".env", ".properties",
    ".xml", ".html", ".htm", ".css", ".scss", ".sql", ".csv", ".tsv", ".rst", ".java",
    ".go", ".rb", ".php", ".rs", ".c", ".h", ".cpp", ".hpp", ".gradle", ".lock",
)

#: 明文凭据正则。命中即**拒绝入库**（R1）。
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|pwd)"
            r"\s*[=:]\s*[\"'][^\"'\s]{12,}[\"']"
        ),
    ),
    ("bearer_header", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{24,}=*")),
)

# 注：``API_KEY_PLACEHOLDER`` / ``PLACEHOLDER_LITERAL`` 由 ``skills.packaging`` 导入，
# 权威定义在那里；这里直接沿用，避免同一字面量在两个模块各写一份而漂移。

#: 需要为"高熵可疑串"豁免的文件名（哈希清单类文件天然高熵且无风险）。
ENTROPY_EXEMPT_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "pipfile.lock", "sha256sums", "checksums.txt",
})
BASE64_LIKE = re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b|\b[A-Za-z0-9_\-]{40,}\b")


@dataclass
class ValidationIssue:
    """一条校验问题。severity=error 会阻止入库，warning 只提示。"""

    code: str
    message: str
    severity: str = "error"
    path: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "severity": self.severity, "path": self.path}


@dataclass
class ValidationReport:
    """预检结果。``ok`` 为 False 时不得进入影子评测。"""

    manifest: dict = field(default_factory=dict)
    files: list = field(default_factory=list)
    package_sha256: str = ""
    issues: list = field(default_factory=list)

    @property
    def errors(self) -> list:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "manifest": self.manifest,
            "files": self.files,
            "package_sha256": self.package_sha256,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


@dataclass
class PreflightResult:
    report: ValidationReport
    token: str = ""
    token_id: str = ""
    expires_at: str = ""
    package_dir: str = ""

    def as_dict(self) -> dict:
        payload = self.report.as_dict()
        payload.update({"token": self.token, "token_id": self.token_id, "expires_at": self.expires_at})
        return payload


# ---------------------------------------------------------------- 暂存区

def preflight_root() -> Path:
    """预检暂存区根目录（位于 MEDIA_ROOT 下，不对外暴露为媒体路径）。"""
    root = Path(settings.MEDIA_ROOT) / PREFLIGHT_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def cleanup_preflight(*, token_id: str | None = None) -> None:
    """清理暂存包：给了 token_id 就删单个，否则清理所有已过期条目。"""
    root = preflight_root()
    if token_id:
        shutil.rmtree(root / token_id, ignore_errors=True)
        return
    cutoff = timezone.now().timestamp() - PREFLIGHT_TTL_SECONDS
    for entry in root.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


# ---------------------------------------------------------------- 安全解压

def safe_extract_zip(zip_file, dest_dir: str) -> dict:
    """把 ZIP 解压到指定目录，并在解压过程中拦截危险条目。

    与 ``Skill._safe_extract_zip`` 相比额外检查：硬链接、单文件体积、路径长度与
    目录深度、压缩比（zip bomb 启发式）。返回统计信息供校验报告使用。
    """
    dest = Path(dest_dir).resolve(strict=False)
    file_count = 0
    total_size = 0
    compressed_size = 0

    with zipfile.ZipFile(zip_file, "r") as archive:
        for info in archive.infolist():
            raw_name = (info.filename or "").replace("\\", "/")
            if not raw_name or raw_name.endswith("/"):
                continue

            file_count += 1
            if file_count > MAX_FILES:
                raise ValidationError(f"包内文件数超过上限（{MAX_FILES}）")

            file_size = int(getattr(info, "file_size", 0) or 0)
            total_size += file_size
            compressed_size += int(getattr(info, "compress_size", 0) or 0)
            if file_size > MAX_SINGLE_FILE:
                raise ValidationError(f"包内单文件超过上限（{MAX_SINGLE_FILE // 1024 // 1024}MB）：{raw_name}")
            if total_size > MAX_TOTAL_UNCOMPRESSED:
                raise ValidationError(f"解压后总体积超过上限（{MAX_TOTAL_UNCOMPRESSED // 1024 // 1024}MB）")

            posix = PurePosixPath(raw_name)
            if posix.is_absolute() or any(part == ".." for part in posix.parts):
                raise ValidationError(f"包内包含非法路径：{raw_name}")
            if posix.parts and ":" in posix.parts[0]:
                raise ValidationError(f"包内包含非法路径：{raw_name}")
            if len(raw_name) > MAX_PATH_LENGTH:
                raise ValidationError(f"包内路径过长（>{MAX_PATH_LENGTH}）：{raw_name[:80]}...")
            if len(posix.parts) > MAX_PATH_DEPTH:
                raise ValidationError(f"包内目录层级过深（>{MAX_PATH_DEPTH}）：{raw_name}")

            mode = (info.external_attr or 0) >> 16
            # 符号链接会指向包外任意路径；硬链接可绕过路径检查指向其他文件。
            if stat.S_ISLNK(mode):
                raise ValidationError(f"包内包含不支持的符号链接：{raw_name}")
            if stat.S_ISREG(mode) and (getattr(info, "external_attr", 0) or 0) and _looks_like_hardlink(info):
                raise ValidationError(f"包内包含不支持的硬链接：{raw_name}")

            target = (dest / Path(*posix.parts)).resolve(strict=False)
            try:
                if os.path.commonpath([str(dest), str(target)]) != str(dest):
                    raise ValidationError(f"包内包含非法路径：{raw_name}")
            except ValueError:
                raise ValidationError(f"包内包含非法路径：{raw_name}")

            archive.extract(info, str(dest))

    if compressed_size > 0 and total_size / compressed_size > MAX_COMPRESSION_RATIO:
        raise ValidationError(
            f"包压缩比异常（{total_size / compressed_size:.0f}:1），疑似 zip bomb"
        )

    return {"file_count": file_count, "total_size": total_size, "compressed_size": compressed_size}


def _looks_like_hardlink(info) -> bool:
    """ZIP 里硬链接的惯例标记：Unix 文件类型位为 S_IFREG 但 external_attr 高 16 位非 0。"""
    mode = (info.external_attr or 0) >> 16
    return bool(mode) and stat.S_ISREG(mode) and not (mode & 0o777)


# ---------------------------------------------------------------- 扫描

def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_high_entropy(token: str) -> bool:
    """判断是否为可疑的随机凭据串：足够长、熵高、且大小写数字混杂。"""
    if len(token) < 32 or len(token) > 256:
        return False
    has_lower = any(c.islower() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_lower and has_upper and has_digit):
        return False
    return _shannon_entropy(token) >= 4.5


def _read_text(path: Path) -> str | None:
    """按 UTF-8 读取文本文件；非文本或编码非法返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _is_text_candidate(relative: str) -> bool:
    lowered = relative.lower()
    name = os.path.basename(lowered)
    if name in {"skill.md", "readme", "readme.md", "license"}:
        return True
    if name.startswith(".env"):
        return True
    return lowered.endswith(TEXT_SUFFIXES)


#: 文件名即凭据的常见清单（不需要读内容就能判定）。
CREDENTIAL_FILE_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".git-credentials",
})

#: 证书/私钥类后缀。
CREDENTIAL_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer")


def _iter_all_files(root: Path) -> list:
    """列出包内**全部**常规文件（不套 ``packaging`` 的排除规则）。

    凭据检测必须看全量：``iter_package_files`` 会排除 ``.env`` / ``id_rsa`` / ``*.pem``，
    但"被排除"不等于"被接受"——含凭据的包应当被**明确拒绝并告知原因**，而不是
    静默忽略后让它留在暂存区里。
    """
    collected: list = []
    for current_root, _dir_names, file_names in os.walk(root):
        for file_name in file_names:
            absolute = os.path.join(current_root, file_name)
            if os.path.islink(absolute) or not os.path.isfile(absolute):
                continue
            collected.append(os.path.relpath(absolute, root).replace(os.sep, "/"))
    return collected


def _credential_file_issue(relative: str) -> ValidationIssue | None:
    """文件本身就是凭据/私钥时返回一条 error，否则返回 None。"""
    lowered = relative.lower()
    name = os.path.basename(lowered)
    if name.startswith(".env") or name in CREDENTIAL_FILE_NAMES:
        return ValidationIssue(
            code="credential_file", severity="error", path=relative,
            message=f"包内包含凭据文件 {relative}，禁止入库",
        )
    if lowered.endswith(CREDENTIAL_FILE_SUFFIXES):
        return ValidationIssue(
            code="credential_file", severity="error", path=relative,
            message=f"包内包含证书/私钥文件 {relative}，禁止入库",
        )
    return None


def scan_package(root: Path, *, manifest: dict, files: list) -> list:
    """扫描已解压的包目录，返回问题清单（error 阻止入库）。

    ``files`` 是**参与入库**的文件（已按 ``packaging`` 规则排除缓存/日志/凭据）；
    凭据文件单独用全量清单检测，理由见 ``_iter_all_files``。
    """
    issues: list = []
    declared_permissions = manifest.get("permissions") or []
    if not isinstance(declared_permissions, list):
        declared_permissions = []
    allow_executables = "allow_executables" in declared_permissions

    # 1) 凭据类文件按文件名/后缀直接拦（全量检测，不受排除规则影响）。
    credential_paths: set = set()
    for relative in _iter_all_files(root):
        issue = _credential_file_issue(relative)
        if issue is not None:
            issues.append(issue)
            credential_paths.add(relative)

    for relative in files:
        if relative in credential_paths:
            continue
        absolute = root / relative
        lowered = relative.lower()
        name = os.path.basename(lowered)

        # 2) 二进制可执行类型默认拒绝，除非 manifest 显式声明权限。
        if lowered.endswith(BLOCKED_BINARY_SUFFIXES):
            issues.append(ValidationIssue(
                code="blocked_binary",
                severity="warning" if allow_executables else "error",
                path=relative,
                message=(
                    f"包内包含二进制/可执行文件 {relative}"
                    + ("（manifest 已声明 allow_executables）" if allow_executables else "，禁止入库")
                ),
            ))
            continue

        if not _is_text_candidate(relative):
            continue

        # 3) UTF-8 可读性。
        text = _read_text(absolute)
        if text is None:
            issues.append(ValidationIssue(
                code="not_utf8", severity="error", path=relative,
                message=f"{relative} 不是合法的 UTF-8 文本",
            ))
            continue

        # 脱敏占位符不是凭据：先归一化再扫，保证"导出的包可以原样重新上传"。
        text = PLACEHOLDER_LITERAL.sub('""', text)

        # 4) 明文凭据。
        for code, pattern in CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append(ValidationIssue(
                    code=f"secret:{code}", severity="error", path=relative,
                    message=f"{relative} 中检测到疑似明文凭据（{code}），位置 {match.start()}",
                ))
                break

        # 5) 高熵可疑串（跳过哈希清单类文件，避免误报）。
        if name not in ENTROPY_EXEMPT_NAMES:
            for candidate in BASE64_LIKE.findall(text):
                if _looks_high_entropy(candidate):
                    issues.append(ValidationIssue(
                        code="secret:high_entropy", severity="error", path=relative,
                        message=f"{relative} 中检测到高熵可疑串（长度 {len(candidate)}），疑似密钥",
                    ))
                    break

    return issues


def scan_contents(contents: dict, *, skip_codes: frozenset | tuple = ()) -> list:
    """对 ``{相对路径: bytes}`` 做明文凭据扫描（导出前的二次脱敏检查）。

    与 ``scan_package`` 的区别：这里的输入是**即将写入导出包的内容**，因此任何命中
    都意味着"这份包不该被下载"，需要转为 ``quarantined``。

    Args:
        skip_codes: 本次扫描跳过的规则名（``CREDENTIAL_PATTERNS`` 的第一列）。
            导出阶段会跳过 ``assigned_secret``——平台自己就往内部 Skill 里注入
            API Key，那条规则会把平台注入值判成泄漏，让内部 Skill 永远导不出去；
            赋值型凭据的防泄漏由导出的反向脱敏承担（值会被换成占位符，不会外泄）。
            具有强特征的凭据（``sk-`` / ``AKIA`` / 私钥 / JWT / 高熵串）不跳过，
            它们必须被识别出来并触发隔离，而不是被"顺手抹掉"后正常放行。
    """
    issues: list = []
    for relative, raw in (contents or {}).items():
        if not _is_text_candidate(relative):
            continue
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except UnicodeDecodeError:
            continue

        # 脱敏占位符不是凭据：先归一化再扫，保证"导出的包可以原样重新上传"。
        text = PLACEHOLDER_LITERAL.sub('""', text)

        for code, pattern in CREDENTIAL_PATTERNS:
            if code in skip_codes:
                continue
            match = pattern.search(text)
            if match:
                issues.append(ValidationIssue(
                    code=f"secret:{code}", severity="error", path=relative,
                    message=f"导出内容在 {relative} 中命中明文凭据规则（{code}），已阻止下载",
                ))
                break

        name = os.path.basename(relative.lower())
        if name in ENTROPY_EXEMPT_NAMES:
            continue
        for candidate in BASE64_LIKE.findall(text):
            if _looks_high_entropy(candidate):
                issues.append(ValidationIssue(
                    code="secret:high_entropy", severity="error", path=relative,
                    message=f"导出内容在 {relative} 中命中高熵可疑串，已阻止下载",
                ))
                break
    return issues


def parse_manifest(root: Path) -> tuple[dict, list]:
    """解析 SKILL.md 的 YAML frontmatter，返回 (manifest, issues)。"""
    import yaml

    issues: list = []
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        issues.append(ValidationIssue(code="missing_entry", message="包根目录缺少 SKILL.md"))
        return {}, issues

    try:
        content = skill_md.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        issues.append(ValidationIssue(code="not_utf8", path="SKILL.md", message=f"SKILL.md 无法按 UTF-8 读取：{exc}"))
        return {}, issues

    if not content.startswith("---"):
        issues.append(ValidationIssue(code="bad_frontmatter", path="SKILL.md", message="SKILL.md 必须以 YAML frontmatter（---）开头"))
        return {}, issues

    parts = content.split("---", 2)
    if len(parts) < 3:
        issues.append(ValidationIssue(code="bad_frontmatter", path="SKILL.md", message="SKILL.md frontmatter 缺少结束标记"))
        return {}, issues

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        issues.append(ValidationIssue(code="bad_frontmatter", path="SKILL.md", message=f"frontmatter YAML 解析失败：{exc}"))
        return {}, issues

    if not isinstance(frontmatter, dict):
        issues.append(ValidationIssue(code="bad_frontmatter", path="SKILL.md", message="frontmatter 必须是对象"))
        return {}, issues

    manifest = dict(frontmatter)
    for key in ("name", "description"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(ValidationIssue(code="manifest_incomplete", path="SKILL.md", message=f"manifest 缺少必填字符串字段 {key}"))
        else:
            manifest[key] = value.strip()
    return manifest, issues


def validate_manifest(root: Path, manifest: dict) -> list:
    """校验 manifest 的完整性、阶段合法性与 Schema/入口可解析性。"""
    issues: list = []

    for key in REQUIRED_MANIFEST_FIELDS:
        if key not in manifest:
            issues.append(ValidationIssue(code="manifest_incomplete", path="SKILL.md", message=f"manifest 缺少必填字段 {key}"))

    for key in RECOMMENDED_MANIFEST_FIELDS:
        if key not in manifest:
            issues.append(ValidationIssue(
                code="manifest_recommended_missing", severity="warning", path="SKILL.md",
                message=f"manifest 未声明 {key}（允许，缺省时按指令型 Skill 处理）",
            ))

    version = manifest.get("version")
    if isinstance(version, str) and version.strip():
        if not re.match(r"^[0-9]+(\.[0-9]+){1,3}([-+][A-Za-z0-9.\-]+)?$", version.strip()):
            issues.append(ValidationIssue(
                code="bad_version", path="SKILL.md",
                message=f"version 必须形如 1.2.0（可带预发布后缀），当前为 {version!r}",
            ))
    elif "version" in manifest:
        issues.append(ValidationIssue(code="bad_version", path="SKILL.md", message="version 必须是非空字符串"))

    stage = manifest.get("stage")
    if isinstance(stage, str) and stage.strip():
        if stage.strip() not in VALID_STAGES:
            issues.append(ValidationIssue(
                code="bad_stage", path="SKILL.md",
                message=f"未知能力阶段 {stage!r}，合法值：{sorted(VALID_STAGES)}",
            ))
    elif "stage" in manifest:
        issues.append(ValidationIssue(code="bad_stage", path="SKILL.md", message="stage 必须是非空字符串"))

    entrypoint = manifest.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint.strip():
        target = (root / entrypoint.strip()).resolve(strict=False)
        try:
            inside = os.path.commonpath([str(root.resolve()), str(target)]) == str(root.resolve())
        except ValueError:
            inside = False
        if not inside:
            issues.append(ValidationIssue(code="bad_entrypoint", path="SKILL.md", message=f"entrypoint 越出包目录：{entrypoint}"))
        elif not target.exists():
            issues.append(ValidationIssue(code="bad_entrypoint", path="SKILL.md", message=f"entrypoint 不存在：{entrypoint}"))
    elif "entrypoint" in manifest:
        issues.append(ValidationIssue(code="bad_entrypoint", path="SKILL.md", message="entrypoint 必须是非空字符串"))

    for key in ("input_schema", "output_schema"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        target = (root / value.strip()).resolve(strict=False)
        if not str(target).startswith(str(root.resolve())):
            issues.append(ValidationIssue(code="bad_schema", path="SKILL.md", message=f"{key} 越出包目录：{value}"))
            continue
        if not target.is_file():
            issues.append(ValidationIssue(code="bad_schema", path="SKILL.md", message=f"{key} 指向的文件不存在：{value}"))
            continue
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            issues.append(ValidationIssue(code="bad_schema", path=value, message=f"{key} 不是可解析的 JSON Schema：{exc}"))

    permissions = manifest.get("permissions")
    if "permissions" in manifest and not isinstance(permissions, list):
        issues.append(ValidationIssue(code="bad_permissions", path="SKILL.md", message="permissions 必须是列表"))

    return issues


def scan_package_dir(root: Path) -> ValidationReport:
    """对已解压目录做完整校验（不含解压动作），返回校验报告。

    包哈希按 ``redact_secrets=True`` 计算（把 API Key 字面量归一成占位符后再算）。
    这不是"少算了什么"，而是唯一能让三方对上的口径：

    - 平台给内部 Skill 注入真实 Key 之后，落盘内容的哈希不会漂移；
    - 导出时反向脱敏得到的是占位符形态，重新上传算出的哈希与首次入库相同，
      于是能幂等命中同一版本，而不是生成一个"看起来一样"的重复版本。

    代价是"两个包只有 API Key 值不同"会被视为同一份内容。这是有意的：Key 属于
    环境配置，不属于可复用内容，不该参与内容指纹。
    """
    manifest, issues = parse_manifest(root)
    files = iter_package_files(root)
    issues.extend(validate_manifest(root, manifest) if manifest else [])
    issues.extend(scan_package(root, manifest=manifest, files=files))
    return ValidationReport(
        manifest=manifest,
        files=files,
        package_sha256=compute_package_sha256(root, redact_secrets=True),
        issues=issues,
    )


# ---------------------------------------------------------------- 服务

class SkillPackageValidationService:
    """预检与暂存包消费（T05）。"""

    @staticmethod
    def preflight(*, zip_file, filename: str = "") -> PreflightResult:
        """安全解压到暂存区并返回校验报告与短期令牌（不写正式版本库）。"""
        cleanup_preflight()
        token_id = uuid.uuid4().hex
        staging_dir = preflight_root() / token_id
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            try:
                safe_extract_zip(zip_file, str(staging_dir))
            except zipfile.BadZipFile:
                return PreflightResult(
                    report=ValidationReport(issues=[ValidationIssue(code="bad_zip", message="无效的 ZIP 文件")])
                )
            except ValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                return PreflightResult(
                    report=ValidationReport(issues=[ValidationIssue(code="unsafe_archive", message=message)])
                )

            report = scan_package_dir(staging_dir)
            if not report.ok:
                # 未通过的包不留在暂存区，避免占用磁盘并防止被误消费。
                shutil.rmtree(staging_dir, ignore_errors=True)
                return PreflightResult(report=report)

            expires_at = timezone.now() + timezone.timedelta(seconds=PREFLIGHT_TTL_SECONDS)
            token = signing.dumps(
                {
                    "token_id": token_id,
                    "package_sha256": report.package_sha256,
                    "manifest_name": report.manifest.get("name", ""),
                    "manifest_version": report.manifest.get("version", ""),
                    "original_filename": os.path.basename(filename or ""),
                },
                salt=PREFLIGHT_SALT,
            )
            return PreflightResult(
                report=report, token=token, token_id=token_id,
                expires_at=expires_at.isoformat(), package_dir=str(staging_dir),
            )
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    @staticmethod
    def consume_preflight(*, token: str) -> tuple[dict, Path]:
        """校验令牌并返回 (载荷, 暂存目录)；令牌过期/篡改/暂存缺失均抛 ValidationError。"""
        try:
            payload = signing.loads(token, salt=PREFLIGHT_SALT, max_age=PREFLIGHT_TTL_SECONDS)
        except signing.SignatureExpired:
            raise ValidationError("预检令牌已过期，请重新上传校验")
        except signing.BadSignature:
            raise ValidationError("预检令牌无效")

        staging_dir = preflight_root() / str(payload.get("token_id") or "")
        if not staging_dir.is_dir():
            raise ValidationError("预检暂存内容不存在或已被清理，请重新上传校验")

        # 重新校验哈希：防止"预检通过后包被替换"的 TOCTOU 场景。
        # 口径与 scan_package_dir 一致（归一化后计算），否则正常包也会被误判为被替换。
        current_sha = compute_package_sha256(staging_dir, redact_secrets=True)
        if current_sha != payload.get("package_sha256"):
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValidationError("预检令牌与暂存包哈希不一致，请重新上传校验")

        return payload, staging_dir
