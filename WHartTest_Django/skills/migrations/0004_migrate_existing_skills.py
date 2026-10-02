"""把存量 Skill 迁移为初始不可变版本（T04 / R13）。

R13 要求 **为每条** ``Skill`` 记录生成初始不可变版本，因此这里对两类记录分别处理：

**A. 包目录完整**（``skill_path`` 存在且目录可读）
   - 计算包内容哈希，建立 ``kind="skill"`` 的 ``CapabilityRelease`` 与 ``SkillVersion``。
   - 原 ``is_active=True`` 时把版本置为 ``active`` 并写入 ``Skill.active_version``。
   - 不移动、不复制、不删除任何原文件。

**B. 只有内联 ``skill_content``、没有落盘目录**（历史遗留的坏数据）
   - 仍然建立版本记录，但 ``package_path`` 留空、``manifest.inline_only=True``，
     状态固定为 ``draft``，**绝不设为 active**——因为按 R13，运行时没有活跃版本就
     必须拒绝执行，不能让一个没有包的记录被当作可运行版本。
   - 包哈希按"仅含一份 SKILL.md 的包"计算，将来把这些内容真正落盘后哈希可复现。
   - 校验报告写入 ``needs_package_rebuild``，作为可查询的异常报告（而非只打印一次）。

迁移是**可重入**的：重复执行不会产生重复版本；若同一版本号的包哈希与磁盘现状
不一致，直接抛错停止，绝不覆盖既有版本。

老包没有 ``stage`` / ``entrypoint`` 等字段，manifest 里显式标记
``needs_manual_stage_binding``，避免它们被误当作可评测候选直接进入影子评测。
"""
import hashlib
import logging
import os
from pathlib import Path

from django.conf import settings
from django.db import migrations

logger = logging.getLogger(__name__)

MIGRATED_VERSION = "0.0.0-migrated"

# 与 skills/packaging.py 保持一致的排除规则（迁移自包含，见模块 docstring）。
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", "venv", ".venv", "env",
    "dist", "build", ".idea", ".vscode", ".DS_Store",
})
EXCLUDED_FILE_NAMES = frozenset({
    ".ds_store", "thumbs.db", "credentials", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".git-credentials",
})
EXCLUDED_FILE_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".temp", ".swp", ".swo",
    ".bak", ".orig", ".rej", ".key", ".pem", ".crt", ".cer", ".p12", ".pfx",
    ".sqlite", ".sqlite3", ".db",
)
EXCLUDED_FILE_PREFIXES = (".env",)


def _is_excluded_dir(name: str) -> bool:
    return name.lower() in EXCLUDED_DIR_NAMES or name in EXCLUDED_DIR_NAMES


def _is_excluded_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in EXCLUDED_FILE_NAMES:
        return True
    if any(lowered.startswith(prefix) for prefix in EXCLUDED_FILE_PREFIXES):
        return True
    return lowered.endswith(EXCLUDED_FILE_SUFFIXES)


def _iter_package_files(root: Path) -> list:
    """列出包内参与哈希的相对 POSIX 路径，按 UTF-8 字节序排序。"""
    collected: list = []
    for current_root, dir_names, file_names in os.walk(root, followlinks=False):
        dir_names[:] = [
            d for d in dir_names
            if not _is_excluded_dir(d) and not os.path.islink(os.path.join(current_root, d))
        ]
        for file_name in file_names:
            if _is_excluded_file(file_name):
                continue
            absolute = os.path.join(current_root, file_name)
            if os.path.islink(absolute) or not os.path.isfile(absolute):
                continue
            collected.append(os.path.relpath(absolute, root).replace(os.sep, "/"))
    collected.sort(key=lambda item: item.encode("utf-8"))
    return collected


def _compute_package_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _iter_package_files(root):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            content = (root / relative).read_bytes()
        except OSError:
            content = b""
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_single_skill_md(content: bytes) -> str:
    """按包哈希算法计算"仅含一份 SKILL.md"的包哈希。

    多文件哈希 = 逐文件 ``路径 + NUL + 长度 + NUL + 内容 + NUL`` 顺序拼接，
    这里单文件同样遵循，保证将来把内联内容真正落盘成只含 SKILL.md 的包目录后，
    ``compute_package_sha256`` 能得到同一结果。
    """
    digest = hashlib.sha256()
    digest.update(b"SKILL.md")
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)
    digest.update(b"\0")
    return digest.hexdigest()


def _resolve_package_dir(skill) -> Path | None:
    """把 skill.skill_path 解析为 MEDIA_ROOT 内的绝对目录，越界则返回 None。"""
    if not skill.skill_path:
        return None
    try:
        media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
        candidate = Path(os.path.join(settings.MEDIA_ROOT, skill.skill_path)).resolve(strict=False)
        # 条件：解析结果逃逸出 MEDIA_ROOT；动作：拒绝；结果：避免误把系统目录当包目录。
        if media_root != candidate and media_root not in candidate.parents:
            return None
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_dir() else None


def migrate_existing_skills(apps, schema_editor):
    Skill = apps.get_model("skills", "Skill")
    SkillVersion = apps.get_model("skills", "SkillVersion")
    CapabilityRelease = apps.get_model("knowledge_evolution", "CapabilityRelease")

    created = 0
    reused = 0
    incomplete: list = []

    for skill in Skill.objects.all().order_by("created_at"):
        package_dir = _resolve_package_dir(skill)
        inline_content = (skill.skill_content or "").encode("utf-8")

        if package_dir is not None:
            package_sha256 = _compute_package_sha256(package_dir)
            package_complete = True
        else:
            package_sha256 = _hash_single_skill_md(inline_content)
            package_complete = False

        existing = SkillVersion.objects.filter(skill_id=skill.id, version=MIGRATED_VERSION).first()
        if existing is not None:
            # 条件：同版本已存在但哈希不同；动作：抛错停止；结果：不覆盖已入库版本。
            if existing.package_sha256 != package_sha256:
                raise RuntimeError(
                    f"Skill {skill.name!r}({skill.id}) 的 {MIGRATED_VERSION} 版本已存在，"
                    f"但内容哈希已变化（库内 {existing.package_sha256} != 现在 {package_sha256}）。"
                    "迁移不覆盖既有版本，请人工确认后新增版本。"
                )
            reused += 1
            # 补齐活跃指针：仅"包完整 + 原记录启用"才允许指向该版本。
            if package_complete and skill.is_active and skill.active_version_id != existing.id:
                Skill.objects.filter(pk=skill.pk).update(active_version_id=existing.id)
            continue

        warnings: list = ["老包缺少 stage/entrypoint/Schema，需人工补齐后才能进入影子评测"]
        if not package_complete:
            warnings.append(
                "该 Skill 没有落盘的包目录，仅有内联 SKILL.md；已生成 draft 版本占位，"
                "需人工重建包目录后才能激活"
            )

        release = CapabilityRelease.objects.create(
            project_id=skill.project_id,
            kind="skill",
            name=skill.name,
            version=MIGRATED_VERSION,
            config={
                "schema_version": "skill-package/v1",
                "source_type": "migration",
                "skill_id": str(skill.id),
            },
            artifact_hash=package_sha256,
            # 包不完整的记录一律 draft：运行时按 R13 拒绝在无活跃版本时执行。
            state="active" if (package_complete and skill.is_active) else "draft",
        )
        version = SkillVersion.objects.create(
            skill_id=skill.id,
            version=MIGRATED_VERSION,
            release=release,
            package_path=skill.skill_path if package_complete else "",
            package_sha256=package_sha256,
            manifest={
                "name": skill.name,
                "description": skill.description,
                "version": MIGRATED_VERSION,
                "stage": "",
                "entrypoint": "",
                "input_schema": "",
                "output_schema": "",
                "permissions": [],
                "migrated": True,
                "inline_only": not package_complete,
                "needs_package_rebuild": not package_complete,
                "needs_manual_stage_binding": True,
            },
            validation_report={
                "migrated": True,
                "package_complete": package_complete,
                "checks": [
                    {
                        "name": "package_readable",
                        "passed": package_complete,
                        "detail": "存量包目录可读" if package_complete else "未找到落盘包目录",
                    },
                    {
                        "name": "inline_skill_md_present",
                        "passed": bool(inline_content),
                        "detail": f"内联 SKILL.md 长度 {len(inline_content)} 字节",
                    },
                ],
                "warnings": warnings,
            },
            source_type="migration",
            source_metadata={
                "skill_path": skill.skill_path,
                "original_is_active": bool(skill.is_active),
                "original_skill_content_sha256": hashlib.sha256(inline_content).hexdigest(),
            },
            created_by_id=skill.creator_id,
        )
        if package_complete and skill.is_active:
            Skill.objects.filter(pk=skill.pk).update(active_version_id=version.id)
        if not package_complete:
            incomplete.append(
                f"skill_id={skill.id} name={skill.name!r} project={skill.project_id} "
                f"skill_path={skill.skill_path!r}"
            )
        created += 1

    # 迁移必须有可核对的证据输出：migrate 的 stdout 即为验收材料的一部分。
    print(
        f"[T04] 存量 Skill 迁移完成：新建 {created} 个 {MIGRATED_VERSION} 版本"
        f"（其中包完整 {created - len(incomplete)} 个、仅内联 {len(incomplete)} 个），"
        f"复用 {reused} 个已存在版本。"
    )
    if incomplete:
        print("[T04] 以下 Skill 没有落盘包目录，已生成 draft 版本占位，需人工重建包目录后才能激活：")
        for item in incomplete:
            print(f"  - {item}")


def rollback_migration(apps, schema_editor):
    """回滚本迁移：只删除迁移生成的版本与发布单元，保留原 Skill 记录与文件。"""
    Skill = apps.get_model("skills", "Skill")
    SkillVersion = apps.get_model("skills", "SkillVersion")
    CapabilityRelease = apps.get_model("knowledge_evolution", "CapabilityRelease")

    version_ids = list(
        SkillVersion.objects.filter(
            version=MIGRATED_VERSION, source_type="migration"
        ).values_list("id", flat=True)
    )
    release_ids = list(
        SkillVersion.objects.filter(id__in=version_ids).values_list("release_id", flat=True)
    )
    # 先清活跃指针，避免外键 SET_NULL 之外的悬挂引用。
    Skill.objects.filter(active_version_id__in=version_ids).update(active_version=None)
    SkillVersion.objects.filter(id__in=version_ids).delete()
    CapabilityRelease.objects.filter(id__in=[r for r in release_ids if r]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("skills", "0003_skill_capability_skillversion_skill_active_version_and_more"),
        ("knowledge_evolution", "0023_remove_capabilityrelease_uniq_capability_release_version_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_existing_skills, rollback_migration),
    ]
