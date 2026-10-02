"""Skill 版本的候选创建、差异比较与生命周期操作（T06 / T07）。

统一入口把三个来源（本地上传、Git 导入、Skill 商店）收敛到同一条路径：

    preflight / 目录校验  ->  create_candidate  ->  不可变版本目录落盘

关键约束：

- **幂等**：同一 Skill 下同一包哈希只入库一次，重复提交返回已有版本，不重复落盘。
- **不可变**：每个版本一个独立目录（``skills/{project}/versions/{skill}/{version}__{sha16}``），
  与历史包根目录**平级**、互不嵌套，保证旧包的哈希不被新版本目录污染。
- **不自动激活**：新建版本一律 ``draft``（R7「候选不得绕过评测与审批进入生产」），
  连"某个 Skill 的第一个版本"也不例外。
- **审计**：创建、隔离、下载都写 ``KnowledgeAuditLog``。
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from knowledge_evolution.capability_models import CapabilityRelease
from knowledge_evolution.capabilities import CapabilityReleaseService, assert_can_transition
from knowledge_evolution.knowledge_models import KnowledgeAuditLog

from .models import Skill, SkillVersion
from .packaging import compute_package_sha256, iter_package_files
from .validation import (
    SkillPackageValidationService,
    ValidationIssue,
    ValidationReport,
    scan_package_dir,
)

logger = logging.getLogger(__name__)

#: 版本包目录模板。刻意放在 ``{project_id}/versions/`` 下、与历史包根平级：
#: 若嵌在包根内部，旧包哈希会把新版本目录一起算进去，导致同一内容哈希漂移。
VERSION_STORAGE_TEMPLATE = "skills/{project_id}/versions/{skill_id}/{version}__{digest}"

#: 单个文件的文本 diff 上限，超出则只给结构差异，避免 diff 接口被大文件拖垮。
MAX_TEXT_DIFF_BYTES = 20_000


def version_storage_path(*, project_id, skill_id, version: str, package_sha256: str) -> str:
    """计算版本的不可变存储目录（相对 MEDIA_ROOT）。"""
    digest = (package_sha256 or "")[:16]
    return VERSION_STORAGE_TEMPLATE.format(
        project_id=project_id, skill_id=skill_id, version=version, digest=digest
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()


def _snapshot(root: Path) -> dict:
    """把包目录快照成 {相对路径: (内容哈希, 大小)}。"""
    snapshot = {}
    if not root or not root.is_dir():
        return snapshot
    for relative in iter_package_files(root):
        absolute = root / relative
        try:
            size = absolute.stat().st_size
        except OSError:
            size = 0
        snapshot[relative] = (_file_digest(absolute), size)
    return snapshot


def compute_diff(base_version, candidate_version) -> dict:
    """计算两个版本的文件级差异，附带可阅读的文本 diff。

    ``base_version`` 为 None 时表示首个版本，全部文件都算"新增"。
    """
    base_root = Path(base_version.get_full_path()) if (base_version and base_version.get_full_path()) else None
    candidate_root = Path(candidate_version.get_full_path()) if candidate_version.get_full_path() else None
    if candidate_root is None or not candidate_root.is_dir():
        raise ValidationError("候选版本没有可读取的包目录，无法比较差异")

    base_snapshot = _snapshot(base_root)
    candidate_snapshot = _snapshot(candidate_root)

    added = sorted(set(candidate_snapshot) - set(base_snapshot))
    removed = sorted(set(base_snapshot) - set(candidate_snapshot))
    modified = []
    unchanged = 0
    for relative in sorted(set(base_snapshot) & set(candidate_snapshot)):
        base_digest, base_size = base_snapshot[relative]
        cand_digest, cand_size = candidate_snapshot[relative]
        if base_digest == cand_digest:
            unchanged += 1
            continue
        modified.append({
            "path": relative,
            "base_sha256": base_digest,
            "candidate_sha256": cand_digest,
            "base_size": base_size,
            "candidate_size": cand_size,
        })

    text_diffs = _text_diffs(base_root, candidate_root, [item["path"] for item in modified])

    return {
        "base": _version_ref(base_version),
        "candidate": _version_ref(candidate_version),
        "files": {
            "added": added,
            "removed": removed,
            "modified": modified,
            "unchanged_count": unchanged,
        },
        "text_diffs": text_diffs,
        "summary": f"新增 {len(added)}、删除 {len(removed)}、修改 {len(modified)}、未变 {unchanged}",
    }


def _version_ref(version) -> dict:
    if version is None:
        return {}
    return {
        "id": str(version.id),
        "version": version.version,
        "package_sha256": version.package_sha256,
        "state": version.state,
    }


def _text_diffs(base_root, candidate_root, paths: list) -> list:
    """对文本文件生成 unified diff；二进制或超大文件只记结构差异。"""
    results = []
    for relative in paths:
        candidate_file = candidate_root / relative
        try:
            if candidate_file.stat().st_size > MAX_TEXT_DIFF_BYTES:
                results.append({"path": relative, "skipped": "文件过大，仅记录结构差异"})
                continue
            candidate_text = candidate_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            results.append({"path": relative, "skipped": "非文本文件，仅记录结构差异"})
            continue

        base_text = ""
        if base_root and (base_root / relative).is_file():
            try:
                base_text = (base_root / relative).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                base_text = ""

        diff_lines = list(difflib.unified_diff(
            base_text.splitlines(), candidate_text.splitlines(),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="",
        ))
        results.append({"path": relative, "unified_diff": "\n".join(diff_lines)[:MAX_TEXT_DIFF_BYTES]})
    return results


class SkillVersionService:
    """候选版本创建与生命周期（T06）。"""

    # ------------------------------------------------------------ 创建

    @classmethod
    def create_candidate_from_dir(
        cls, *, source_dir, project, actor=None, source_type: str = "upload",
        source_metadata: dict | None = None, change_reason: str = "",
        expected_benefit: str = "", impact_scope: str = "",
        api_key: str | None = None,
    ) -> tuple:
        """对已解压的目录做校验并创建候选版本（三个来源共用）。

        ``api_key`` 只在安装内部平台 Skill 时使用，注入时机被刻意固定在
        **校验与取哈希之后、落盘之前**（见 ``_inject_api_key`` 的说明）。

        Returns:
            (Skill, SkillVersion)
        """
        source_dir = Path(source_dir)
        if not source_dir.is_dir():
            raise ValidationError("候选包目录不存在")

        report = scan_package_dir(source_dir)
        if not report.ok:
            detail = "；".join(issue.message for issue in report.errors[:5])
            raise ValidationError(f"包校验未通过：{detail}")

        manifest = report.manifest
        name = manifest["name"]
        # 哈希必须取"用户提交内容"的指纹：注入 API Key 之前的这份包。
        # 否则导出（已脱敏）后重新上传会算出不同哈希，无法幂等命中同一版本。
        package_sha256 = report.package_sha256

        cls._inject_api_key(source_dir=source_dir, skill_name=name, api_key=api_key)

        with transaction.atomic():
            skill, created_skill = cls._get_or_create_skill(
                project=project, actor=actor, manifest=manifest, source_dir=source_dir,
            )

            # 幂等：同一 Skill 下同一份内容只入库一次。
            existing = SkillVersion.objects.filter(
                skill=skill, package_sha256=package_sha256
            ).first()
            if existing is not None:
                logger.info("重复包命中已有版本，幂等返回：skill=%s sha=%s", skill.id, package_sha256[:12])
                return skill, existing

            # 版本号：manifest 没声明时由平台补全。存量指令型 Skill 大多没有 version，
            # 若直接拒绝，历史包就无法重新上传（与 T07"重新上传恢复相同定义"冲突）。
            declared_version = (manifest.get("version") or "").strip()
            version = declared_version or cls._auto_version(skill)
            if not declared_version:
                manifest = {**manifest, "version": version, "version_inferred": True}

            # 版本号冲突：同一 Skill 下版本号唯一，内容不同就必须换号，不能覆盖。
            if SkillVersion.objects.filter(skill=skill, version=version).exists():
                raise ValidationError(
                    f"版本号 {version} 已存在且内容不同，请提升版本号后重新提交（已入库版本不可覆盖）"
                )

            previous = cls._latest_version(skill)
            package_path = version_storage_path(
                project_id=project.id, skill_id=skill.id,
                version=version, package_sha256=package_sha256,
            )
            cls._materialize(source_dir=source_dir, package_path=package_path)

            release = CapabilityRelease.objects.create(
                project=project,
                kind="skill",
                name=skill.name,
                version=version,
                config={
                    "schema_version": "skill-package/v1",
                    "source_type": source_type,
                    "skill_id": str(skill.id),
                    "stage": manifest.get("stage", ""),
                    "package_sha256": package_sha256,
                },
                artifact_hash=package_sha256,
                # 一律 draft：候选不得绕过评测与负责人审批直接进入生产。
                state="draft",
                created_by=actor,
            )
            skill_version = SkillVersion.objects.create(
                skill=skill,
                version=version,
                release=release,
                package_path=package_path,
                package_sha256=package_sha256,
                manifest=manifest,
                validation_report=report.as_dict(),
                source_type=source_type,
                source_metadata={
                    **(source_metadata or {}),
                    "change_reason": change_reason,
                    "expected_benefit": expected_benefit,
                    "impact_scope": impact_scope,
                    # 回滚目标 = 上一版本，激活失败或生产回归时回到它。
                    "rollback_target": str(previous.id) if previous else "",
                },
                previous_version=previous,
                created_by=actor,
            )

        KnowledgeAuditLog.record(
            project_id=project.id, action="create", actor=actor,
            entity=skill_version,
            from_state="", to_state="draft",
            reason=change_reason or f"创建候选版本 {skill.name}@{version}",
            detail={
                "skill_id": str(skill.id), "skill_name": skill.name, "version": version,
                "package_sha256": package_sha256, "source_type": source_type,
                "previous_version": str(previous.id) if previous else "",
                "skill_created": created_skill,
            },
        )
        return skill, skill_version

    @classmethod
    def create_candidate_from_preflight(
        cls, *, token: str, project, actor=None, source_metadata: dict | None = None,
        change_reason: str = "", expected_benefit: str = "", impact_scope: str = "",
        api_key: str | None = None,
    ) -> tuple:
        """消费预检令牌并创建候选版本（令牌过期/篡改/哈希不符都会拒绝）。"""
        payload, staging_dir = SkillPackageValidationService.consume_preflight(token=token)
        try:
            metadata = {
                **(source_metadata or {}),
                "original_filename": payload.get("original_filename", ""),
                "preflight_token_id": payload.get("token_id", ""),
            }
            return cls.create_candidate_from_dir(
                source_dir=staging_dir, project=project, actor=actor,
                source_type="upload", source_metadata=metadata,
                change_reason=change_reason, expected_benefit=expected_benefit,
                impact_scope=impact_scope, api_key=api_key,
            )
        finally:
            # 无论成功与否都释放暂存区，避免残留未引用的临时包。
            shutil.rmtree(staging_dir, ignore_errors=True)

    # ------------------------------------------------------------ 辅助

    @staticmethod
    def _inject_api_key(*, source_dir: Path, skill_name: str, api_key: str | None) -> None:
        """为内部平台 Skill 注入 API Key（非内部 Skill 直接跳过）。

        为什么注入放在校验与取哈希之后：

        1. **扫描只面向用户提交物**。若先注入再扫描，平台自己写进去的密钥会被
           ``CREDENTIAL_PATTERNS`` 命中，导致内部 Skill 永远无法入库。
        2. **哈希与密钥解耦**。``package_sha256`` 表示用户提交内容，导出时反向脱敏
           即可还原成同一份提交物，重新上传能幂等命中，而不是生成重复版本。
        """
        from .platform_skills import inject_api_key_into_skill_dir, is_internal_platform_skill

        if not is_internal_platform_skill(skill_name):
            return
        if not (api_key or "").strip():
            raise ValidationError(
                f"安装内部 Skill '{skill_name}' 需要确认 API Key，请选择或创建后重试"
            )
        try:
            inject_api_key_into_skill_dir(
                skill_dir=str(source_dir), skill_name=skill_name, api_key=api_key.strip(),
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @staticmethod
    def verify_package_integrity(version) -> dict:
        """核验磁盘上的版本包是否仍与入库哈希一致。

        落盘包可能含平台注入的 API Key，因此用 ``redact_secrets=True`` 重算：
        注入会被还原成占位符而"隐形"，任何其它字节改动仍会被发现。
        """
        root = version.get_full_path()
        if not root or not Path(root).is_dir():
            return {"ok": False, "reason": "package_dir_missing", "expected": version.package_sha256}
        actual = compute_package_sha256(root, redact_secrets=True)
        return {
            "ok": actual == version.package_sha256,
            "expected": version.package_sha256,
            "actual": actual,
            "reason": "" if actual == version.package_sha256 else "package_tampered",
        }

    @staticmethod
    def _get_or_create_skill(*, project, actor, manifest: dict, source_dir: Path) -> tuple:
        """按项目 + Skill 名称取逻辑身份；不存在则新建（保留旧字段以兼容旧运行时）。"""
        skill = Skill.objects.filter(project=project, name=manifest["name"]).first()
        if skill is not None:
            return skill, False

        skill_md = source_dir / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""

        skill = Skill.objects.create(
            project=project,
            creator=actor,
            name=manifest["name"],
            description=manifest.get("description", ""),
            skill_content=content,
            # skill_path 是"当前活跃版本目录"的指针，激活时才写入。
            # 留空不等于不可用：运行时按版本记录解析（活跃版本优先，
            # 没有活跃版本就用最新的可运行版本），这个字段只服务于旧调用方。
            skill_path="",
            is_active=True,
        )
        return skill, True

    @staticmethod
    def find_derived_candidate(skill, *, derivation_fingerprint: str):
        """按"派生指纹"查是否已经派生过同一批改动，返回已存在的候选版本或 ``None``。

        为什么必须按指纹查、不能按包哈希查：候选落库前版本号一定会被推进，
        同一批归因重复派生产出的包哈希天然不同，事后比对哈希永远判不出重复。
        "到底改了什么"这件事只记录在指纹里，所以查询也必须落在指纹上。

        只认 ``source_type='evolution'``：指纹字段是派生链路自己写的，
        上传/导入通道的版本没有这个语义，不该被卷进来。
        """
        if not derivation_fingerprint:
            return None
        return (
            SkillVersion.objects
            .filter(
                skill=skill, source_type="evolution",
                source_metadata__derivation_fingerprint=derivation_fingerprint,
            )
            .order_by("-created_at")
            .first()
        )

    @staticmethod
    def latest_version(skill) -> SkillVersion | None:
        """该 Skill 下最近创建的版本（公开入口，供派生/回滚计算基线）。"""
        return SkillVersionService._latest_version(skill)

    @staticmethod
    def next_version(skill) -> str:
        """为派生候选计算一个未被占用的版本号（公开入口）。

        为什么派生必须换版本号：``SkillVersion`` 的唯一约束是 ``(skill, version)``，
        同一个号只允许对应一份内容。派生出的包内容与基线必然不同，若沿用基线的号，
        会被"版本号已存在且内容不同"直接拒绝——这不是约束太严，而是**包内 manifest
        与库内版本号必须一致**：否则导出这个包再传回来，manifest 声明的旧号会与
        库里已有的旧号撞车，重新上传永远无法成功。
        """
        return SkillVersionService._auto_version(skill)

    @staticmethod
    def _latest_version(skill) -> SkillVersion | None:
        return skill.versions.order_by("-created_at").first()

    @staticmethod
    def _auto_version(skill) -> str:
        """为"manifest 未声明版本号"的包补一个可用版本号。

        规则：在已有版本里找最大的 ``X.Y.Z``，补丁号 +1；找不到就 ``1.0.0``。
        会跳过已被占用的号，避免补出来的号与历史版本撞车。
        """
        if skill is None or not getattr(skill, "pk", None):
            return "1.0.0"
        used = set(skill.versions.values_list("version", flat=True))
        if not used:
            return "1.0.0"

        numeric = []
        for item in used:
            match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", (item or "").strip())
            if match:
                numeric.append(tuple(int(group) for group in match.groups()))
        if not numeric:
            # 已有版本都不是 X.Y.Z 形式（如 0.0.0-migrated），从一个干净的号重新起算。
            candidate = "1.0.0"
            suffix = 1
            while candidate in used:
                candidate = f"1.0.{suffix}"
                suffix += 1
            return candidate

        major, minor, patch = max(numeric)
        for _ in range(len(used) + 2):
            patch += 1
            candidate = f"{major}.{minor}.{patch}"
            if candidate not in used:
                return candidate
        return f"{major}.{minor}.{patch}"

    @staticmethod
    def _materialize(*, source_dir: Path, package_path: str) -> None:
        """把包内容原子落盘到不可变目录；已存在则视为重复落盘，直接返回。"""
        target = Path(settings.MEDIA_ROOT) / package_path
        if target.is_dir():
            # 目录已存在说明同一内容已落盘过（哈希目录名幂等），不覆盖。
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".staging-{target.name}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            shutil.copytree(source_dir, staging, symlinks=False)
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    # ------------------------------------------------------------ 生命周期

    @staticmethod
    def diff(base_version, candidate_version) -> dict:
        """版本差异（公开入口）。"""
        return compute_diff(base_version, candidate_version)

    @staticmethod
    @transaction.atomic
    def quarantine(version, *, actor, reason: str) -> SkillVersion:
        """隔离版本：停止运行时加载，并把关联发布单元一并隔离。"""
        if not (reason or "").strip():
            raise ValidationError("隔离必须填写原因")
        previous_state = version.state
        if version.release_id:
            CapabilityReleaseService.quarantine(version.release, actor=actor, reason=reason)
        # 隔离后该 Skill 不得再指向这个版本。
        Skill.objects.filter(pk=version.skill_id, active_version_id=version.id).update(
            active_version=None
        )
        KnowledgeAuditLog.record(
            project_id=version.skill.project_id, actor=actor,
            action="quarantine", entity=version,
            from_state=previous_state, to_state="quarantined", reason=reason,
            detail={"version": version.version, "package_sha256": version.package_sha256},
        )
        version.refresh_from_db()
        return version

    @staticmethod
    def submit_for_validation(version, *, actor, reason: str = ""):
        """把草稿候选送进影子验证：重跑静态校验，并把发布单元推进到 ``shadow``。

        为什么是"重跑"而不是"沿用预检结论"：预检发生在**临时暂存区**，正式版本目录
        是另一次落盘。若两者之间包内容被替换，只信旧结论就等于给一份未经校验的包
        放行。这里对**不可变版本目录**重新扫描，校验对象与运行时将要加载的对象
        是同一份。

        状态推进严格走状态图的两条合法边（``draft->validating``、``validating->shadow``）；
        校验失败走 ``validating->rejected``，并在异常里逐条列出问题，供控制台展示
        "到底哪一条没过"，而不是一句"校验失败"。

        （说明：T18 之前没有任何代码路径会驱动 draft/validating 这两格，
        于是候选创建后永远停在草稿、控制台的审批按钮永远点不亮。这个缺口是
        开发控制台时暴露出来的，修在这里而不是前端。）
        """
        state = version.state
        if state in ("shadow", "awaiting_approval", "active"):
            # 幂等：已经过了静态校验这一关，重复提交不重扫、也不回退状态。
            return ValidationReport(**{
                key: value
                for key, value in (version.validation_report or {}).items()
                if key in {"manifest", "files", "package_sha256"}
            }), version
        if state not in ("draft", "validating"):
            raise ValidationError(f"当前状态（{state}）不允许提交静态校验")

        release = version.release
        package_dir = version.get_full_path()
        if not package_dir or not Path(package_dir).is_dir():
            raise ValidationError("版本包目录不存在，无法校验")

        # 状态推进与校验报告放在同一事务：结论与证据要么一起落，要么都不落。
        with transaction.atomic():
            release.refresh_from_db()
            # draft -> validating：先落这一步，审计里能看出它走到了校验阶段。
            if release.state == "draft":
                assert_can_transition(release.state, "validating")
                release.state = "validating"
                release.save(update_fields=["state", "updated_at"])

            report = scan_package_dir(Path(package_dir))
            # 包内容重算出的哈希必须与入库时一致，否则说明版本目录被改动过。
            if report.package_sha256 and version.package_sha256 \
                    and report.package_sha256 != version.package_sha256:
                report.issues.append(
                    ValidationIssue(
                        code="package_hash_drift",
                        message="版本包内容与入库哈希不一致，疑似被改动",
                        severity="error",
                    )
                )

            version.validation_report = report.as_dict()
            version.save(update_fields=["validation_report", "updated_at"])

            target = "shadow" if report.ok else "rejected"
            assert_can_transition(release.state, target)
            release.state = target
            release.save(update_fields=["state", "updated_at"])

        # 审计与异常刻意放在事务**之外**：校验未通过时既要留下 rejected 结论和
        # 审计证据，又要把错误抛给调用方。若留在事务里，"抛异常"会把刚写下的
        # 证据一起回滚，控制台就只能显示"草稿"，看不出为什么没过。
        SkillVersionService.record_validate(
            version, actor=actor, report=report.as_dict(), reason=reason,
        )
        if not report.ok:
            detail = "；".join(issue.message for issue in report.errors[:5]) or "未给出具体原因"
            raise ValidationError(f"静态校验未通过：{detail}")
        return report, version

    @staticmethod
    def record_validate(version, *, actor, report: dict, reason: str = "") -> None:
        """记录一次包校验审计（只留统计与问题码，不落包原文）。"""
        KnowledgeAuditLog.record(
            project_id=version.skill.project_id, actor=actor,
            action="validate", entity=version,
            from_state="", to_state=version.state,
            reason=(reason or "").strip() or "候选包静态校验",
            detail={
                "ok": report.get("ok", False),
                "package_sha256": version.package_sha256,
                "error_codes": [item.get("code") for item in report.get("errors", [])],
                "warning_codes": [item.get("code") for item in report.get("warnings", [])],
            },
        )

    @staticmethod
    def record_download(version, *, actor) -> None:
        """记录一次下载审计（不保存包原文，只留哈希与动作）。"""
        KnowledgeAuditLog.record(
            project_id=version.skill.project_id, actor=actor,
            action="download", entity=version,
            from_state=version.state, to_state=version.state,
            reason="导出脱敏 Skill 包",
            detail={
                "version": version.version,
                "package_sha256": version.package_sha256,
                "filename": version.manifest.get("name", version.skill.name),
            },
        )


def delete_version_files(version) -> None:
    """删除版本目录与版本记录（仅用于维护场景；正常流程不允许删除已入库版本）。"""
    full_path = version.get_full_path()
    if full_path:
        shutil.rmtree(full_path, ignore_errors=True)
    version.delete()
