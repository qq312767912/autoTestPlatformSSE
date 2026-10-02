import logging
import os
import stat
import uuid
import zipfile
import yaml
import shutil
from pathlib import Path, PurePosixPath
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from projects.models import Project

logger = logging.getLogger(__name__)


def skill_upload_path(instance, filename):
    """Skill 文件上传路径"""
    # 基于项目与 Skill ID 分层存储，便于后续按项目清理文件。
    return f'skills/{instance.project.id}/{instance.id}/{filename}'


class Skill(models.Model):
    """
    Skill 模型，存储用户上传的 Agent Skill
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='skills',
        verbose_name=_('所属项目')
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_skills',
        verbose_name=_('创建人')
    )
    name = models.CharField(
        _('Skill 名称'),
        max_length=255,
        help_text='Skill 的唯一标识名称'
    )
    description = models.TextField(
        _('Skill 描述'),
        help_text='描述 Skill 的功能和使用场景'
    )
    skill_content = models.TextField(
        _('SKILL.md 内容'),
        blank=True,
        help_text='SKILL.md 文件的完整内容'
    )
    skill_path = models.CharField(
        _('Skill 存储路径'),
        max_length=500,
        blank=True,
        help_text='Skill 文件解压后的存储路径'
    )
    is_active = models.BooleanField(
        _('是否启用'),
        default=True
    )
    capability = models.ForeignKey(
        'knowledge_evolution.CapabilityDefinition',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='skills',
        verbose_name=_('绑定能力'),
        help_text='一个 Skill 绑定一个可进化能力定义，用于评测与发布治理',
    )
    active_version = models.ForeignKey(
        'SkillVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_for_skills',
        verbose_name=_('当前活跃版本'),
        help_text='运行时快速解析用的活跃版本指针；权威状态仍以版本关联的发布单元为准',
    )
    # 「阶段未声明」的补救入口（Skill Hub 上由管理员补填）。
    # 为什么不写进版本 manifest：版本包是不可变产物，改写 manifest 会让包哈希对不上
    # （package_tampered），自进化会在"基线包被篡改"处中止。
    # 为什么不复用 capability：CapabilityDefinition 是重量级的能力注册表
    # （kind / evaluation_mode / gate_rules / active_release），拿它当一个
    # "阶段标签"用会扭曲语义。所以单独放一个轻量声明位。
    # 解析顺序：版本 manifest 声明的 stage 优先，缺失时回落到这里。
    declared_stage = models.CharField(
        _('声明能力阶段'),
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        help_text='manifest 未声明阶段的 Skill 由管理员补填，取值见 capability_registry.STAGE_LABELS',
    )
    created_at = models.DateTimeField(_('创建时间'), auto_now_add=True)
    updated_at = models.DateTimeField(_('更新时间'), auto_now=True)

    class Meta:
        verbose_name = _('Skill')
        verbose_name_plural = _('Skills')
        ordering = ['-created_at']
        unique_together = ('project', 'name')

    def __str__(self):
        return f"{self.name} ({self.project.name})"

    def get_full_path(self):
        """获取 Skill 的完整文件系统路径（始终返回绝对路径）"""
        from django.conf import settings
        if self.skill_path:
            path = os.path.join(settings.MEDIA_ROOT, self.skill_path)
            return os.path.abspath(path)
        return None

    def get_script_path(self):
        """获取可执行脚本路径（支持 .py 和 .js）"""
        full_path = self.get_full_path()
        if not full_path:
            return None

        for root, dirs, files in os.walk(full_path):
            # 跳过缓存与依赖目录，减少无效扫描和误识别。
            dirs[:] = [d for d in dirs if d not in ('__pycache__', 'node_modules')]
            for f in files:
                if f.startswith('__'):
                    continue
                if f.endswith('.py') or f.endswith('.js'):
                    # 返回首个可执行脚本路径，满足“快速发现入口脚本”场景。
                    return os.path.join(root, f)
        return None

    @staticmethod
    def _safe_extract_zip(
        zf: zipfile.ZipFile,
        dest_dir: str,
        *,
        max_files: int = 2000,
        max_total_size: int = 50 * 1024 * 1024,
    ) -> None:
        """安全解压 zip，防止 Zip Slip / 超量解压 / 符号链接"""
        dest_path = Path(dest_dir).resolve(strict=False)
        file_count = 0
        total_size = 0

        for info in zf.infolist():
            name = (info.filename or "").replace("\\", "/")

            if not name or name.endswith("/"):
                continue

            file_count += 1
            # 文件数量超限直接拒绝，防止 zip bomb 大量小文件消耗 inode/IO。
            if file_count > max_files:
                raise ValidationError("zip 文件包含过多文件")

            total_size += int(getattr(info, "file_size", 0) or 0)
            # 解压总体积超限直接拒绝，避免磁盘被恶意压缩包打满。
            if total_size > max_total_size:
                raise ValidationError("zip 解压后总大小超出限制")

            posix = PurePosixPath(name)
            if posix.is_absolute() or any(part == ".." for part in posix.parts):
                raise ValidationError("zip 文件包含非法路径")
            if posix.parts and ":" in posix.parts[0]:
                raise ValidationError("zip 文件包含非法路径")

            mode = (info.external_attr or 0) >> 16
            # 禁止符号链接，避免解压后指向任意系统路径。
            if stat.S_ISLNK(mode):
                raise ValidationError("zip 文件包含不支持的符号链接")

            target_path = (dest_path / Path(*posix.parts)).resolve(strict=False)
            try:
                # 二次确保目标路径位于目标目录内，防止 Zip Slip。
                if os.path.commonpath([str(dest_path), str(target_path)]) != str(dest_path):
                    raise ValidationError("zip 文件包含非法路径")
            except ValueError:
                raise ValidationError("zip 文件包含非法路径")

            zf.extract(info, str(dest_path))

    @classmethod
    def parse_skill_md(cls, content: str) -> dict:
        """
        解析 SKILL.md 内容，提取 YAML frontmatter

        Returns:
            dict: {'name': ..., 'description': ..., 'body': ...}
        """
        if not content.startswith('---'):
            raise ValidationError('SKILL.md 必须以 YAML frontmatter 开头 (---)')

        parts = content.split('---', 2)
        if len(parts) < 3:
            raise ValidationError('SKILL.md 格式无效，缺少 YAML frontmatter 结束标记')

        try:
            frontmatter = yaml.safe_load(parts[1])
        except yaml.YAMLError as e:
            raise ValidationError(f'YAML frontmatter 解析失败: {e}')

        if not frontmatter:
            raise ValidationError('YAML frontmatter 为空')

        if not isinstance(frontmatter, dict):
            raise ValidationError('YAML frontmatter 必须是对象 (mapping)')

        raw_name = frontmatter.get('name', '')
        raw_description = frontmatter.get('description', '')
        # 显式限制类型，避免将复杂对象注入数据库字段。
        if not isinstance(raw_name, str) or not isinstance(raw_description, str):
            raise ValidationError('SKILL.md 的 name/description 必须是字符串')

        name = raw_name.strip()
        description = raw_description.strip()

        if not name:
            raise ValidationError('SKILL.md 缺少 name 字段')
        if not description:
            raise ValidationError('SKILL.md 缺少 description 字段')

        return {
            'name': name,
            'description': description,
            'body': parts[2].strip()
        }

    @classmethod
    def create_from_zip(
        cls,
        zip_file,
        project: Project,
        creator: User,
        api_key: str | None = None,
        source_type: str = 'upload',
        source_metadata: dict | None = None,
    ) -> list['Skill']:
        """
        从上传的 zip 文件创建一个或多个 Skill

        Args:
            zip_file: 上传的 zip 文件对象
            project: 所属项目
            creator: 创建者
            api_key: 内部平台 Skill 安装时用户确认的 API Key
            source_type: 版本来源标记（upload / git / store），用于审计与溯源
            source_metadata: 额外来源信息（原始文件名、提交号、商店条目等）

        Returns:
            成功导入的 Skill 列表
        """
        import tempfile

        from .validation import safe_extract_zip

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # 用统一的 safe_extract_zip（比本模型原有的 _safe_extract_zip 更严：
                # 额外拦硬链接、单文件体积、路径长度与层级、压缩比），保证三个导入
                # 来源在"什么算危险包"上口径完全一致。
                safe_extract_zip(zip_file, temp_dir)
            except zipfile.BadZipFile:
                raise ValidationError('无效的 zip 文件')

            skill_dirs = cls._find_skill_dirs(temp_dir)
            if not skill_dirs:
                raise ValidationError('zip 文件中未找到 SKILL.md')

            created_skills: list[Skill] = []
            errors: list[str] = []

            for skill_dir in skill_dirs:
                try:
                    skill = cls._create_skill_from_dir(
                        skill_dir, project, creator, api_key=api_key,
                        source_type=source_type,
                        source_metadata=source_metadata or {
                            'original_filename': getattr(zip_file, 'name', '') or '',
                        },
                    )
                    created_skills.append(skill)
                except ValidationError as e:
                    msg = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
                    errors.append(msg)

            if not created_skills:
                raise ValidationError(
                    f"所有 Skills 导入失败: {'; '.join(errors)}" if errors
                    else 'zip 文件中未找到有效的 SKILL.md'
                )

            if errors:
                logger.warning("部分 Skills 上传失败: %s", '; '.join(errors))

            return created_skills

    @classmethod
    def _clone_repo(cls, git_url: str, branch: str, dest_dir: str) -> None:
        """浅克隆 Git 仓库到指定目录"""
        import subprocess
        from urllib.parse import urlparse

        git_url = (git_url or '').strip()
        branch = (branch or 'main').strip() or 'main'

        parsed_url = urlparse(git_url)
        if parsed_url.scheme != 'https':
            raise ValidationError('仅支持 HTTPS 协议的仓库地址')
        if not parsed_url.netloc:
            raise ValidationError('无效的 Git 仓库地址')

        path_parts = [p for p in (parsed_url.path or '').split('/') if p]
        if len(path_parts) < 2:
            raise ValidationError('无效的 Git 仓库地址')

        try:
            subprocess.run(
                ['git', 'clone', '--depth', '1', '--branch', branch, git_url, dest_dir],
                check=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise ValidationError('Git 克隆超时（60秒）')
        except FileNotFoundError:
            raise ValidationError('服务器未安装 git，无法导入')
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or '').strip()
            raise ValidationError(f'Git 克隆失败: {stderr}' if stderr else 'Git 克隆失败')

    @classmethod
    def _find_skill_dirs(cls, repo_dir: str) -> list[str]:
        """遍历仓库目录，找到所有包含 SKILL.md 的目录（找到后不再递归其子目录）"""
        skill_dirs = []
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules')]
            if 'SKILL.md' in files:
                skill_dirs.append(root)
                dirs.clear()
        return skill_dirs

    @classmethod
    def _create_skill_from_dir(
        cls,
        skill_root: str,
        project: Project,
        creator: User,
        api_key: str | None = None,
        source_type: str = 'upload',
        source_metadata: dict | None = None,
    ) -> 'Skill':
        """从包含 SKILL.md 的目录创建 Skill 及其候选版本（T06 的唯一收敛点）。

        本地上传、Git 导入、商店 URL 导入三条来源在这里汇合：无论目录从哪来，
        校验、取包哈希、注入 API Key、不可变落盘、建版本记录和写审计都由
        ``SkillVersionService.create_candidate_from_dir`` 统一完成，避免"某个来源
        绕过了密钥扫描"这类只在一条路径上出现的安全缺口。

        与旧实现的差异：同名 Skill 不再报错，而是**追加一个新版本**（R2 不可变版本）。
        内容与已有版本完全相同则由服务层幂等返回已有版本，不会落重复数据。
        """
        from .versions import SkillVersionService

        skill, _version = SkillVersionService.create_candidate_from_dir(
            source_dir=skill_root,
            project=project,
            actor=creator,
            source_type=source_type,
            source_metadata=source_metadata or {},
            api_key=api_key,
        )
        return skill

    @staticmethod
    def _validate_remote_url(url: str) -> str:
        """校验远程 URL 协议、hostname 并阻止 SSRF（私有/回环/链路本地等 IP）"""
        import ipaddress
        import socket
        from urllib.parse import urlparse

        url = (url or '').strip()
        if not url:
            raise ValidationError('URL 不能为空')

        parsed = urlparse(url)
        if parsed.scheme != 'https':
            raise ValidationError('仅支持 HTTPS 协议')

        host = parsed.hostname or ''
        if not host:
            raise ValidationError('无效的 URL：缺少 hostname')

        # 主机名形式直接是 IP 时先校验该 IP，避免后续 DNS 解析失败遮蔽问题。
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValidationError('不允许访问内网/保留地址')
        except ValueError:
            # 不是字面量 IP，走域名解析路径。
            pass

        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise ValidationError(f'无法解析主机名：{e}')

        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            # 任何解析记录命中内网范围都直接拒绝，避免域名混入内网 IP 触发 SSRF。
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValidationError('目标主机解析到内网/保留地址，已拒绝')

        return url

    @classmethod
    def create_from_zip_url(
        cls,
        zip_url: str,
        project: Project,
        creator: User,
        expected_sha256: str | None = None,
        api_key: str | None = None,
    ) -> list['Skill']:
        """
        从远程 HTTPS URL 下载 zip 包并导入 Skills（用于 Skill 商店）

        Args:
            zip_url: zip 包的 HTTPS URL
            project: 所属项目
            creator: 创建者
            expected_sha256: 可选 SHA256 校验和（小写 16 进制 64 位字符）
            api_key: 内部平台 Skill 安装时用户确认的 API Key

        Returns:
            成功导入的 Skill 列表
        """
        import hashlib
        import tempfile

        from django.conf import settings as dj_settings

        try:
            import httpx
        except ImportError:
            raise ValidationError('服务器未安装 httpx，无法下载远程 Skill')

        zip_url = cls._validate_remote_url(zip_url)
        max_size = int(getattr(dj_settings, 'SKILL_STORE_MAX_ZIP_SIZE', 10 * 1024 * 1024))
        timeout = int(getattr(dj_settings, 'SKILL_STORE_DOWNLOAD_TIMEOUT', 60))

        # SpooledTemporaryFile：小文件在内存中，超过阈值后自动落盘，避免占用过多内存。
        buf = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, suffix='.zip')
        downloaded = 0
        sha = hashlib.sha256()

        try:
            try:
                with httpx.stream(
                    'GET', zip_url,
                    timeout=httpx.Timeout(timeout, connect=10.0),
                    follow_redirects=True,
                ) as resp:
                    if resp.status_code != 200:
                        raise ValidationError(f'下载失败：HTTP {resp.status_code}')

                    # Content-Length 在头部时优先用它做超量预判，省下载流量。
                    content_length = resp.headers.get('content-length')
                    if content_length and content_length.isdigit() and int(content_length) > max_size:
                        raise ValidationError(f'zip 文件超过限制（{max_size // 1024 // 1024}MB）')

                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_size:
                            raise ValidationError(f'zip 文件超过限制（{max_size // 1024 // 1024}MB）')
                        sha.update(chunk)
                        buf.write(chunk)
            except httpx.TimeoutException:
                raise ValidationError(f'下载超时（{timeout}秒）')
            except httpx.RequestError as e:
                raise ValidationError(f'下载失败：{e}')

            if downloaded == 0:
                raise ValidationError('下载内容为空')

            if expected_sha256:
                expected = expected_sha256.strip().lower()
                actual = sha.hexdigest()
                # 严格匹配 sha256，避免被恶意中间人替换 zip 内容。
                if expected != actual:
                    raise ValidationError(f'SHA256 校验失败：期望 {expected}，实际 {actual}')

            buf.seek(0)
            # 复用 create_from_zip 的解压与建版本逻辑，避免逻辑重复；
            # 来源标记为 store，让审计能区分"商店安装"和"本地上传"。
            return cls.create_from_zip(
                buf, project, creator, api_key=api_key,
                source_type='store',
                source_metadata={'zip_url': zip_url, 'expected_sha256': expected_sha256 or ''},
            )
        finally:
            buf.close()

    @classmethod
    def create_from_git(
        cls,
        git_url: str,
        project: Project,
        creator: User,
        branch: str = 'main',
        api_key: str | None = None,
    ) -> list['Skill']:
        """
        从公开 Git 仓库导入 Skills（支持仓库包含多个 Skill）

        Returns:
            成功导入的 Skill 列表
        """
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = os.path.join(temp_dir, 'repo')
            cls._clone_repo(git_url, branch, repo_dir)

            skill_dirs = cls._find_skill_dirs(repo_dir)
            if not skill_dirs:
                raise ValidationError('仓库中未找到 SKILL.md')

            created_skills: list[Skill] = []
            errors: list[str] = []

            for skill_dir in skill_dirs:
                try:
                    skill = cls._create_skill_from_dir(
                        skill_dir, project, creator, api_key=api_key,
                        source_type='git',
                        source_metadata={'git_url': git_url, 'branch': branch},
                    )
                    created_skills.append(skill)
                except ValidationError as e:
                    msg = e.messages[0] if hasattr(e, 'messages') and e.messages else str(e)
                    errors.append(msg)

            if not created_skills:
                raise ValidationError(
                    f"所有 Skills 导入失败: {'; '.join(errors)}" if errors
                    else '仓库中未找到有效的 SKILL.md'
                )

            if errors:
                logger.warning("部分 Skills 导入失败: %s", '; '.join(errors))

            return created_skills


#: 明确"不可运行"的发布状态。运行时可加载的判据是**排除法**，不是"必须已激活"。
#:
#: 为什么不是"必须 active"：激活只是可选的钉版手段（把生产版本钉在某一版），
#: 不是可用性前提。上传通道落盘前已经跑过同一套静态校验，所以"没激活"只意味着
#: "还没人指定生产版本"。
#:
#: 真正排除的只有两种：
#: - ``quarantined``：人基于安全事件做出的隔离决策，任何时候都不放行；
#: - ``rejected``：静态校验被驳回，说明落盘包已与入库哈希/校验结论对不上。
UNRUNNABLE_RELEASE_STATES = ('quarantined', 'rejected')


class SkillVersion(models.Model):
    """Skill 的不可变版本包。

    ``Skill`` 是逻辑身份（同一项目内同名唯一），``SkillVersion`` 是某一次入库的
    具体内容快照。入库后**不得原地覆盖**：任何内容变化都必须产生新版本、新包哈希。

    发布状态不在这里复制一份，而是通过 ``release`` 一对一关联一条 ``kind="skill"``
    的 ``CapabilityRelease``，复用统一的发布状态机（草稿/校验/影子/待审批/生效/
    退役/回滚/驳回/隔离）。
    """

    SOURCE_TYPE_CHOICES = [
        ("upload", "本地上传"),
        ("git", "Git 导入"),
        ("store", "Skill 商店"),
        ("evolution", "自进化派生"),
        ("migration", "存量迁移"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name='versions',
        verbose_name=_('所属 Skill'),
    )
    version = models.CharField(
        _('版本号'), max_length=100,
        help_text='SemVer 或可比较的版本字符串，如 1.2.0、0.0.0-migrated',
    )
    release = models.OneToOneField(
        'knowledge_evolution.CapabilityRelease',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='skill_version',
        verbose_name=_('发布单元'),
        help_text='kind=skill 的发布单元，承载本版本的发布状态与门禁快照',
    )
    package_path = models.CharField(
        _('包存储路径'), max_length=500,
        help_text='相对 MEDIA_ROOT 的不可变存储目录',
    )
    package_sha256 = models.CharField(
        _('包哈希'), max_length=64, db_index=True,
        help_text='基于标准化文件清单与内容的 SHA-256（见 skills.packaging）',
    )
    manifest = models.JSONField(
        _('版本清单'), default=dict, blank=True,
        help_text='版本、阶段、入口、输入/输出 Schema、依赖与权限声明',
    )
    validation_report = models.JSONField(
        _('校验报告'), default=dict, blank=True,
        help_text='结构、密钥、静态扫描与 Schema 校验结果',
    )
    source_type = models.CharField(
        _('来源类型'), max_length=16, choices=SOURCE_TYPE_CHOICES,
        default='upload', db_index=True,
    )
    source_metadata = models.JSONField(
        _('来源元数据'), default=dict, blank=True,
        help_text='原始文件名、Git URL/commit、商店条目、上传人等',
    )
    previous_version = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='derived_versions',
        verbose_name=_('父版本'),
        help_text='用于 diff 与回滚的派生来源',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_skill_versions',
        verbose_name=_('创建人'),
    )
    created_at = models.DateTimeField(_('创建时间'), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_('更新时间'), auto_now=True)

    class Meta:
        verbose_name = _('Skill 版本')
        verbose_name_plural = _('Skill 版本')
        ordering = ['-created_at']
        constraints = [
            # 同一 Skill 下版本号唯一，避免出现两个"1.2.0"。
            models.UniqueConstraint(fields=['skill', 'version'], name='uniq_skill_version'),
            # 同一 Skill 下同一份内容只入库一次，重复上传由服务层幂等返回已有版本。
            models.UniqueConstraint(fields=['skill', 'package_sha256'], name='uniq_skill_version_package'),
        ]
        indexes = [
            models.Index(fields=['skill', '-created_at']),
            models.Index(fields=['source_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.skill.name}@{self.version}"

    def get_full_path(self):
        """返回版本包的绝对路径；未落盘时返回 None。"""
        from django.conf import settings
        if not self.package_path:
            return None
        return os.path.abspath(os.path.join(settings.MEDIA_ROOT, self.package_path))

    @property
    def state(self):
        """发布状态，委托给关联的发布单元；未关联时视为草稿。"""
        return self.release.state if self.release_id else 'draft'

    @property
    def is_runnable(self):
        """是否允许被运行时加载（``skills.runtime`` 与展示层共用这一处判据）。

        **与"是否已激活"解耦**：``Skill.active_version`` / ``active`` 表示"指定的
        生产版本"，那是**可选的钉版手段**，不是可用性前提。上传进 Skill 管理的包在
        落盘前已经跑过同一套静态校验（``validation.scan_package_dir``），未激活只
        说明"还没人指定生产版本"，不说明"这个包不能跑"。

        被排除的只有两种（见 ``UNRUNNABLE_RELEASE_STATES``）：隔离与校验驳回。
        另外 ``Skill.is_active=False`` 是"整个 Skill 关掉"的用户开关，同样不可运行。
        """
        if not self.skill.is_active:
            return False
        # 没有包目录的版本不能跑：迁移生成的"只留内联 SKILL.md"记录
        # （``manifest.needs_package_rebuild``）``package_path`` 是空的，运行时
        # 读不到任何文件。这里只看字段、不碰磁盘——解析是热路径，
        # "包有没有被改写"由 ``verify_integrity`` 显式开关负责。
        if not self.package_path:
            return False
        return self.state not in UNRUNNABLE_RELEASE_STATES

    def manifest_stage(self):
        """返回 manifest 中声明的能力阶段（可能为空）。"""
        return (self.manifest or {}).get('stage') or ''

    def manifest_entrypoint(self):
        """返回 manifest 中声明的入口文件路径（可能为空）。"""
        return (self.manifest or {}).get('entrypoint') or ''


@receiver(post_delete, sender=Skill)
def _cleanup_skill_files(sender, instance, **kwargs):
    """删除 Skill 记录后清理磁盘文件（适用于所有删除场景，包括 QuerySet 批量删除和 CASCADE 级联删除）"""
    full_path = instance.get_full_path()
    if not full_path:
        return
    from django.conf import settings
    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    expected_root = (media_root / 'skills' / str(instance.project_id) / str(instance.id)).resolve(strict=False)
    target = Path(full_path).resolve(strict=False)
    # 版本化之后 ``skill_path`` 可能指向"当前活跃版本的不可变目录"，那类目录的清理
    # 归 ``_cleanup_version_files`` 管（由 SkillVersion 的级联删除触发），这里不插手，
    # 也不打无意义的告警。
    if _is_version_package_path(target, media_root):
        return
    # 条件：路径精确匹配预期目录；动作：递归删除；结果：防止误删非 Skill 目录。
    if target == expected_root and target.exists():
        try:
            shutil.rmtree(target)
        except OSError as e:
            logger.warning("Failed to delete Skill files at %s: %s", target, e)
    elif target.exists():
        # 路径异常时仅告警不删除，避免潜在路径拼接错误导致数据破坏。
        logger.warning("Refusing to delete unexpected Skill path: %s (expected %s)", target, expected_root)


def _is_version_package_path(target: Path, media_root: Path) -> bool:
    """判断路径是否位于 ``MEDIA_ROOT/skills/{project}/versions/`` 之下。

    只做纯字符串结构校验（``skills`` / 项目段 / ``versions`` / …），不查库——
    级联删除时 Skill 记录可能已经不可读，查库反而会抛异常。
    """
    try:
        relative_parts = target.relative_to(media_root).parts
    except ValueError:
        return False
    return len(relative_parts) >= 4 and relative_parts[0] == 'skills' and relative_parts[2] == 'versions'


@receiver(post_delete, sender=SkillVersion)
def _cleanup_version_files(sender, instance, **kwargs):
    """删除版本记录后清理其不可变目录。

    只在路径确实落在版本根之下时才动手，避免 ``package_path`` 被写坏时误删别处。
    """
    if not instance.package_path:
        return
    from django.conf import settings
    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    target = (media_root / instance.package_path).resolve(strict=False)
    if not _is_version_package_path(target, media_root):
        logger.warning("Refusing to delete unexpected version path: %s", target)
        return
    shutil.rmtree(target, ignore_errors=True)
