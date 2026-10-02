"""Skill 包的确定性脱敏导出（T07 / R3、R12）。

一个 Skill 版本入库后是不可变的，但包体里可能含着**只能存在于服务端的东西**：
平台在安装内部 Skill 时会往脚本里注入真实 API Key（见 ``skills.platform_skills``）。
这种包一旦原样下载就会把凭据发给所有人，因此导出必须做三件事：

1. **确定性重打包**：文件按 UTF-8 字节序排序、ZIP 时间戳固定为 1980-01-01、
   权限位归一化、不写 extra/comment。同一版本无论导出多少次，字节与 SHA-256 完全一致。
2. **反向脱敏**：把注入的 API Key 还原成 ``API_KEY_PLACEHOLDER``。Key 的替换是
   **条件性**的——包内没有注入痕迹时不改一个字节，这样"导出再上传"能幂等命中同一版本。
3. **导出前二次扫描**：对**即将写进 ZIP 的字节**再扫一遍凭据与高熵串。命中说明
   脱敏没盖住，此时拒绝下载并把版本转为 ``quarantined``（R3、R12）。

导出内容取自版本自己的不可变目录，并且复用 ``packaging.iter_package_files`` 的排除
规则，因此 ``.git``、``node_modules``、``__pycache__``、日志、缓存、``.env*`` 和
密钥证书文件天然不会进入导出包。

**确定性前提**：字节级可复现依赖"同一 zlib 版本 + 同一 Python 版本"。跨 Python 大版本
迁移后，校验和历史导出物的字节一致性需要重新采集基线。
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.http import StreamingHttpResponse

from knowledge_evolution.capabilities import can_reach
from knowledge_evolution.knowledge_models import KnowledgeAuditLog

from .packaging import iter_package_files, redact_bytes
from .validation import scan_contents

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 确定性参数

#: ZIP 条目的固定时间戳（DOS 时间能表示的最小值 1980-01-01 00:00:00）。
#: 不固定它的话，同一份内容在两次导出里会因为"当前时间"不同而产生不同字节。
ZIP_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

#: ``create_system=3`` 表示 Unix，保证外部工具解出的权限位可预期。
ZIP_CREATE_SYSTEM_UNIX = 3
ZIP_CREATE_VERSION = 20
ZIP_DEFLATE_LEVEL = 9

#: 需要保留可执行位的脚本后缀（其余文件统一 0o644）。
EXECUTABLE_SUFFIXES = (
    ".sh", ".bash", ".zsh", ".command", ".ksh",
    ".py", ".pl", ".rb", ".js", ".mjs", ".cjs", ".ts",
)

#: 下载分块大小，避免一次性把整包推给 WSGI 服务器缓冲。
STREAM_CHUNK_SIZE = 64 * 1024

# ---------------------------------------------------------------- 脱敏规则

# 脱敏规则（``redact_text`` / ``redact_bytes``）的权威实现在 ``skills.packaging``：
# 入库哈希要"注入前算、导出后重传还能对上"，脱敏与哈希必须共用同一套规则，
# 因此这里直接复用，不再各写一份。

# ---------------------------------------------------------------- 结果对象
@dataclass
class ExportResult:
    """一次导出的产物与元数据。"""

    payload: bytes
    filename: str
    version_id: str
    skill_id: str
    skill_name: str
    version: str
    package_sha256: str = ""
    export_sha256: str = ""
    file_count: int = 0
    redacted_files: list = field(default_factory=list)
    state: str = ""

    def as_dict(self) -> dict:
        """不含包体的元数据视图，可直接进接口响应与审计。"""
        return {
            "version_id": self.version_id,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "version": self.version,
            "state": self.state,
            "filename": self.filename,
            "package_sha256": self.package_sha256,
            "export_sha256": self.export_sha256,
            "export_bytes": len(self.payload),
            "file_count": self.file_count,
            "redacted_files": list(self.redacted_files),
        }


def _external_attr(relative: str) -> int:
    """归一化 ZIP 条目的外部属性：常规文件 + 固定权限位。"""
    lowered = relative.lower()
    perms = 0o755 if lowered.endswith(EXECUTABLE_SUFFIXES) else 0o644
    return (stat.S_IFREG | perms) << 16


def build_zip(contents: dict) -> bytes:
    """按确定性规则把 ``{相对路径: bytes}`` 打成 ZIP。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_DEFLATE_LEVEL
    ) as archive:
        # 与 packaging.iter_package_files 用同一套排序键：UTF-8 字节序。
        for relative in sorted(contents, key=lambda item: item.encode("utf-8")):
            info = zipfile.ZipInfo(filename=relative, date_time=ZIP_FIXED_TIMESTAMP)
            info.create_system = ZIP_CREATE_SYSTEM_UNIX
            info.create_version = ZIP_CREATE_VERSION
            info.internal_attr = 0
            info.external_attr = _external_attr(relative)
            # 刻意不写 extra / comment：它们会带上平台或时间相关信息，破坏字节可复现。
            archive.writestr(
                info,
                contents[relative],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=ZIP_DEFLATE_LEVEL,
            )
    return buffer.getvalue()


class SkillPackageExporter:
    """Skill 版本的导出（T07）。"""

    # ------------------------------------------------------------ 采集

    @staticmethod
    def collect_contents(version) -> dict:
        """读取版本不可变目录的内容，返回 ``{相对路径: bytes}``。

        排除规则复用 ``packaging.iter_package_files``，不需要另写一份。
        """
        root = version.get_full_path()
        if not root or not Path(root).is_dir():
            raise ValidationError(
                f"版本 {version.skill.name}@{version.version} 的包目录不存在，无法导出"
            )

        root_path = Path(root)
        contents: dict = {}
        for relative in iter_package_files(root_path):
            try:
                contents[relative] = (root_path / relative).read_bytes()
            except OSError as exc:
                raise ValidationError(f"读取包内文件失败：{relative}（{exc}）")
        if not contents:
            raise ValidationError("包目录为空，没有可导出的内容")
        return contents

    # ------------------------------------------------------------ 导出

    @classmethod
    def export_bytes(cls, version, *, actor=None) -> ExportResult:
        """构建导出包（确定性 + 脱敏 + 二次扫描），并写下载审计。

        Raises:
            ValidationError: 包目录缺失、内容为空，或二次扫描命中凭据（此时版本已被隔离）。
        """
        contents = cls.collect_contents(version)

        # 第一道扫描：针对**原始内容**，跳过 assigned_secret。
        #
        # 为什么必须跳过它：平台自己就往内部 Skill 里注入 API Key（形如
        # ``API_KEY = "<真值>"``），assigned_secret 会把注入值判成泄漏，于是内部
        # Skill 一个都导不出去。赋值型凭据的防泄漏由下面的反向脱敏承担——值一定会
        # 被换成占位符，不会外泄；而"这个版本里到底有没有真凭据"要由强特征规则
        # （sk- / AKIA / 私钥 / JWT / 高熵串）来回答。
        #
        # 顺序很关键：先扫原文再脱敏。反过来做的话，脱敏会把泄漏的凭据一并抹掉，
        # 让"风险包转 quarantined"这条验收永远不触发。
        raw_issues = scan_contents(contents, skip_codes={"assigned_secret"})
        if raw_issues:
            cls._reject_and_isolate(version, actor=actor, issues=raw_issues)
            raise ValidationError(
                "导出前敏感扫描未通过，该版本已隔离并禁止下载："
                + "；".join(issue.message for issue in raw_issues[:5])
            )

        redacted: dict = {}
        redacted_files: list = []
        for relative, raw in contents.items():
            updated, changed = redact_bytes(raw)
            redacted[relative] = updated
            if changed:
                redacted_files.append(relative)

        # 第二道扫描：针对**脱敏结果**，确认脱敏没有遗漏（例如 ``password = "x"``
        # 这类不在脱敏规则覆盖范围内的赋值型凭据仍会被这一道拦下）。
        residual_issues = scan_contents(redacted)
        if residual_issues:
            cls._reject_and_isolate(version, actor=actor, issues=residual_issues)
            raise ValidationError(
                "导出内容二次扫描未通过，该版本已隔离并禁止下载："
                + "；".join(issue.message for issue in residual_issues[:5])
            )

        payload = build_zip(redacted)
        result = ExportResult(
            payload=payload,
            filename=cls.export_filename(version),
            version_id=str(version.id),
            skill_id=str(version.skill_id),
            skill_name=version.skill.name,
            version=version.version,
            package_sha256=version.package_sha256,
            export_sha256=hashlib.sha256(payload).hexdigest(),
            file_count=len(redacted),
            redacted_files=redacted_files,
            state=version.state,
        )

        # 延迟导入：versions 依赖 validation，exporter 再依赖 versions，
        # 放在模块顶层会让 import 链路变成环形。
        from .versions import SkillVersionService

        SkillVersionService.record_download(version, actor=actor)
        logger.info(
            "导出 Skill 包：skill=%s version=%s files=%s redacted=%s export_sha256=%s",
            version.skill.name, version.version, result.file_count,
            len(redacted_files), result.export_sha256[:12],
        )
        return result

    @classmethod
    def export_response(cls, version, *, actor=None) -> StreamingHttpResponse:
        """返回流式下载响应，包体分块产出。

        不落临时文件：包上限 50MB（``validation.MAX_TOTAL_UNCOMPRESSED``），
        且导出前已经全量读过一遍做扫描，再写一次磁盘没有收益。
        """
        result = cls.export_bytes(version, actor=actor)
        response = StreamingHttpResponse(
            cls._iter_chunks(result.payload), content_type="application/zip"
        )
        response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        response["Content-Length"] = str(len(result.payload))
        # 把两个哈希都暴露出来：入库哈希用于对齐版本，导出哈希用于核验本文件。
        response["X-Skill-Version-Id"] = result.version_id
        response["X-Package-Sha256"] = result.package_sha256
        response["X-Export-Sha256"] = result.export_sha256
        response["X-Skill-Redacted-Files"] = str(len(result.redacted_files))
        return response

    @staticmethod
    def _iter_chunks(payload: bytes):
        for offset in range(0, len(payload), STREAM_CHUNK_SIZE):
            yield payload[offset:offset + STREAM_CHUNK_SIZE]

    @staticmethod
    def export_filename(version) -> str:
        """下载文件名：``{skill}-{version}.zip``，只保留文件系统安全字符。"""
        def _slug(value: str, fallback: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-")
            return cleaned or fallback

        return f"{_slug(version.skill.name, 'skill')}-{_slug(version.version, '0.0.0')}.zip"

    @staticmethod
    def export_manifest(version) -> dict:
        """列出该版本会被导出的文件（供前端预览与测试核对，不读内容）。"""
        root = version.get_full_path()
        if not root or not Path(root).is_dir():
            return {"version_id": str(version.id), "files": [], "available": False}
        return {
            "version_id": str(version.id),
            "version": version.version,
            "package_sha256": version.package_sha256,
            "available": True,
            "files": iter_package_files(Path(root)),
        }

    # ------------------------------------------------------------ 隔离

    @staticmethod
    def _reject_and_isolate(version, *, actor, issues: list) -> None:
        """导出前扫描命中时把版本转隔离；已终态或已隔离时不重复流转，只补审计。"""
        reason = "导出前敏感扫描命中：" + "；".join(issue.message for issue in issues[:5])
        if version.state == "quarantined":
            logger.error("导出扫描命中但版本已隔离：%s@%s", version.skill.name, version.version)
            return

        if version.release_id and can_reach(version.state, "quarantined"):
            from .versions import SkillVersionService

            SkillVersionService.quarantine(version, actor=actor, reason=reason)
            return

        # rolled_back 等终态无法再转入隔离；此时版本本来就不在生产，补一条审计即可。
        KnowledgeAuditLog.record(
            project_id=version.skill.project_id, actor=actor,
            action="quarantine", entity=version,
            from_state=version.state, to_state=version.state,
            reason=reason,
            detail={
                "version": version.version,
                "package_sha256": version.package_sha256,
                "blocked": True,
                "note": f"版本处于终态 {version.state}，无法再转入隔离，仅记录导出拦截事件",
            },
        )
