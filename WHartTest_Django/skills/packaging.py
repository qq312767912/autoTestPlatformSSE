"""Skill 包的文件清单与包哈希计算（确定性算法真值）。

**这个模块是包哈希的唯一权威实现**。任何需要判断"两个包是否同一个包"的地方
（入库去重、导出复现、运行时哈希校验、数据迁移回填）都必须调用这里，禁止各自
手写遍历逻辑，否则同一份包会算出不同哈希。

算法约定（与设计文档 3.3 一致）：

1. 只取常规文件，跳过符号链接、目录与排除项。
2. 相对路径统一转成 POSIX 形式（``/`` 分隔）并按 **UTF-8 字节序**排序。
3. 逐文件写入 ``路径 + NUL + 内容长度 + NUL + 内容 + NUL``，长度前缀用于消除
   路径与内容拼接边界的歧义（例如 ``a`` + ``bc`` 与 ``ab`` + ``c``）。
4. 不纳入 mtime、权限位、压缩参数等非内容因素，保证同一内容换台机器哈希一致。

**API Key 与哈希的关系**（T06/T07 的关键约定）：

``package_sha256`` 的语义是"**用户提交内容**的指纹"。平台给内部 Skill 注入的
API Key 属于运行时环境配置，不参与入库哈希——入库流程在**注入之前**取哈希。
这样一来，导出包（已反向脱敏）被重新上传时算出的哈希与首次入库完全相同，
可以幂等命中同一版本，而不是又生成一个"看起来一样"的新版本。

需要校验"磁盘上的包有没有被篡改"时用 ``compute_package_sha256(root,
redact_secrets=True)``：它先把注入的 Key 还原成占位符再算，因此既容忍注入、
又能发现任何其它字节改动。
"""
import hashlib
import os
import re
from pathlib import Path

#: 导出脱敏与哈希归一化共用的 API Key 占位符。
#:
#: 选这个字面量而不是 ``${...}`` / ``__...__`` 的原因：它自身**不会**命中任何凭据
#: 正则（全大写 + 下划线、无小写、长度不足 40 个连续字符），所以脱敏后的产物
#: 重新上传时不会被自己的脱敏结果拦下；语义上也明确提示"这里需要填 Key"。
API_KEY_PLACEHOLDER = "PUT_YOUR_WHARTTEST_API_KEY_HERE"

#: 匹配包内**带引号的**占位符字面量，供扫描前豁免使用。
PLACEHOLDER_LITERAL = re.compile(r'["\']' + re.escape(API_KEY_PLACEHOLDER) + r'["\']')

#: 与 ``platform_skills.inject_api_key_into_skill_dir`` 写入的形态一一对应。
#:
#: ``BASE_URL`` 刻意不处理：它不是凭据，而且换成占位符会让"导出再上传"无法恢复
#: 出可用定义，收益为负、代价明确。
_ENV_API_KEY_PATTERN = re.compile(
    r'(os\.environ\.get\(\s*["\']WHARTTEST_API_KEY["\']\s*,\s*)(["\'])(.*?)\2'
)
_HARDCODED_API_KEY_PATTERN = re.compile(
    r'(?m)^([ \t]*API_KEY[ \t]*=[ \t]*)(["\'])(.*?)\2'
)


# 导出与哈希阶段统一排除的目录名：版本库、依赖、缓存、构建产物、编辑器配置。
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", "venv", ".venv", "env",
    "dist", "build", ".idea", ".vscode", ".DS_Store",
})

# 导出与哈希阶段统一排除的文件名（精确匹配，大小写不敏感）。
EXCLUDED_FILE_NAMES = frozenset({
    ".ds_store", "thumbs.db", "credentials", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".git-credentials",
})

# 导出与哈希阶段统一排除的后缀：运行日志、缓存、密钥与证书、运行产物。
EXCLUDED_FILE_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".temp", ".swp", ".swo",
    ".bak", ".orig", ".rej", ".key", ".pem", ".crt", ".cer", ".p12", ".pfx",
    ".sqlite", ".sqlite3", ".db",
)

# 需要排除的文件名模式前缀（例如 .env / .env.local / .env.production）。
EXCLUDED_FILE_PREFIXES = (".env",)


def is_excluded_dir(name: str) -> bool:
    """目录名是否属于排除项。"""
    return name.lower() in EXCLUDED_DIR_NAMES or name in EXCLUDED_DIR_NAMES


def is_excluded_file(name: str) -> bool:
    """文件名是否属于排除项（凭据、日志、缓存、密钥文件）。"""
    lowered = name.lower()
    if lowered in EXCLUDED_FILE_NAMES:
        return True
    if any(lowered.startswith(prefix) for prefix in EXCLUDED_FILE_PREFIXES):
        return True
    return lowered.endswith(EXCLUDED_FILE_SUFFIXES)


def iter_package_files(root, *, use_symlinks: bool = False) -> list:
    """列出包内参与哈希/导出的相对 POSIX 路径，已按 UTF-8 字节序排序。

    Args:
        root: 包根目录。
        use_symlinks: 为 False（默认）时跳过符号链接，避免把指向包外的文件计入。

    Returns:
        list[str]: 形如 ``["SKILL.md", "scripts/main.py"]`` 的相对路径。
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    collected: list = []
    for current_root, dir_names, file_names in os.walk(root_path, followlinks=False):
        # 原地裁剪目录列表，os.walk 不会继续下探被移除的目录。
        dir_names[:] = [
            d for d in dir_names
            if not is_excluded_dir(d) and (use_symlinks or not os.path.islink(os.path.join(current_root, d)))
        ]
        for file_name in file_names:
            if is_excluded_file(file_name):
                continue
            absolute = os.path.join(current_root, file_name)
            if not use_symlinks and os.path.islink(absolute):
                continue
            if not os.path.isfile(absolute):
                continue
            relative = os.path.relpath(absolute, root_path).replace(os.sep, "/")
            collected.append(relative)

    # UTF-8 字节序排序：跨平台（不同 locale 下 locale 排序不一致）结果稳定。
    collected.sort(key=lambda item: item.encode("utf-8"))
    return collected


def redact_text(text: str) -> tuple[str, bool]:
    """把文本里注入的 API Key 换成占位符，返回 ``(新文本, 是否真的改动)``。

    判断"是否改动"用的是替换前后的字面比较，而不是"有没有匹配到正则"：
    已经含占位符的内容再过一次必须保持字节不变，否则重复导出就不确定了。
    """
    updated = _ENV_API_KEY_PATTERN.sub(rf'\1"{API_KEY_PLACEHOLDER}"', text)
    updated = _HARDCODED_API_KEY_PATTERN.sub(rf'\1"{API_KEY_PLACEHOLDER}"', updated)
    return updated, updated != text


def redact_bytes(raw: bytes) -> tuple[bytes, bool]:
    """字节级脱敏；非 UTF-8 内容原样返回（二进制里不会有注入的字符串常量）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, False
    updated, changed = redact_text(text)
    if not changed:
        return raw, False
    return updated.encode("utf-8"), True


def compute_package_sha256(root, *, redact_secrets: bool = False) -> str:
    """计算包目录的内容哈希（64 位小写十六进制）。

    同一份内容在不同机器、不同时间计算结果一致；换任意一个字节都会变化。

    Args:
        redact_secrets: 为 True 时先把注入的 API Key 还原成占位符再参与哈希。
            用于**校验已落盘的包**——落盘包可能含平台注入的 Key，直接算会与
            入库时的 ``package_sha256`` 不一致，而脱敏后算既能容忍注入又能发现
            任何其它改动。入库取哈希时应保持默认 False（注入尚未发生）。
    """
    digest = hashlib.sha256()
    root_path = Path(root)
    for relative in iter_package_files(root_path):
        absolute = root_path / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            content = absolute.read_bytes()
        except OSError:
            # 读不到的文件（权限/竞态删除）按空内容参与，保持哈希可计算且不抛错。
            content = b""
        if redact_secrets:
            content, _ = redact_bytes(content)
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def compute_manifest_sha256(manifest: dict) -> str:
    """计算 manifest 的规范化哈希，用于比对同一版本的 manifest 是否被改写。"""
    import json

    payload = json.dumps(manifest or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
