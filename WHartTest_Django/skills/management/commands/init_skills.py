"""
预置 Skills 自动初始化命令

每次部署时自动将 bundled_skills 目录中的 Skill 同步到系统。
- 新 Skill：创建数据库记录并复制文件
- 已有 Skill：更新内容和文件（保留用户的 is_active 设置）
- 已激活到版本包的 Skill：**整体跳过**，见 ``_is_immutable_package_target``

为什么必须跳过第三类：Skill 激活后 ``skill_path`` 会被指向**当前活跃版本的
不可变目录**（``MEDIA_ROOT/skills/{project}/versions/{skill}/…``）。那个目录的内容
就是该版本入库时登记的 ``package_sha256``，是该版本唯一的内容权威。若预置同步
仍然按 ``get_full_path()`` 往里写，就会在每次容器启动时把镜像自带的同名技能
覆盖进版本包，导致两个后果：

1. 版本包哈希漂移，``verify_package_integrity`` 永久报 ``package_tampered``，
   该版本再也无法被派生/回滚链路信任；
2. skill 自进化（派生候选会以活跃版本为基线并校验基线未被改动）从第二次起
   一律被"基线包被篡改"中止——不是并发问题，而是每次重启都在重新制造它。

正确行为是：版本化 Skill 的内容更新只能走**版本发布通道**（上传/派生新版本），
预置同步不得旁路覆盖。机器重启不该具有"悄悄改动活跃版本包"的副作用。
"""

import os
import shutil
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()

EXCLUDE_PATTERNS = {
    '.venv', 'venv', '__pycache__', 'node_modules', '.git',
    '.mypy_cache', '.pytest_cache', '.DS_Store', 'Thumbs.db',
}


def _is_immutable_package_target(full_path: str) -> bool:
    """判断同步目标是否落在不可变的版本包目录里。

    判定复用 ``skills.models._is_version_package_path`` 的结构规则（纯字符串比较、
    不查库），保证"哪些目录不可写"这件事只有一处定义，不会两处漂移。
    """
    if not full_path:
        return False
    from skills.models import _is_version_package_path

    media_root = Path(settings.MEDIA_ROOT).resolve(strict=False)
    target = Path(full_path).resolve(strict=False)
    return _is_version_package_path(target, media_root)


def _sync_files(src_dir: str, dst_dir: str):
    """将源目录文件同步到目标目录（覆盖更新，不删除目标中的运行时产物）"""
    os.makedirs(dst_dir, exist_ok=True)

    for item in os.listdir(src_dir):
        # 跳过虚拟环境、缓存和依赖目录，避免把运行时垃圾同步进媒体目录。
        if item in EXCLUDE_PATTERNS:
            continue
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        if os.path.isdir(src):
            shutil.copytree(
                src, dst, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*EXCLUDE_PATTERNS),
            )
        else:
            shutil.copy2(src, dst)


class Command(BaseCommand):
    help = '从 bundled_skills 目录同步预置 Skills（新增或更新）'

    def add_arguments(self, parser):
        # 支持通过参数或环境变量指定预置 Skills 目录。
        parser.add_argument(
            '--skills-dir',
            default=os.environ.get('BUNDLED_SKILLS_DIR', '/app/bundled_skills'),
            help='预置 Skills 目录路径（默认 /app/bundled_skills）',
        )

    def handle(self, *args, **options):
        skills_dir = options['skills_dir']

        # 条件：目录不存在；动作：告警并退出；结果：部署环境可无 bundled_skills 也不中断启动流程。
        if not os.path.isdir(skills_dir):
            self.stdout.write(self.style.WARNING(
                f'预置 Skills 目录不存在: {skills_dir}，跳过初始化'
            ))
            return

        from skills.models import Skill
        from projects.models import Project

        admin_user = User.objects.filter(is_superuser=True).order_by('id').first()
        # 条件：无管理员用户；动作：跳过导入；结果：避免创建“无归属”Skill 记录。
        if not admin_user:
            self.stdout.write(self.style.WARNING('未找到管理员用户，跳过 Skills 初始化'))
            return

        project = Project.objects.order_by('id').first()
        # 条件：系统尚无项目；动作：创建默认项目；结果：预置 Skill 有合法项目归属。
        if not project:
            project = Project.objects.create(
                name='默认项目',
                description='系统自动创建的默认项目',
                owner=admin_user,
            )
            self.stdout.write(self.style.SUCCESS(f'创建默认项目: {project.name}'))

        created_count = 0
        updated_count = 0

        for entry in sorted(os.listdir(skills_dir)):
            entry_path = os.path.join(skills_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            skill_md_path = os.path.join(entry_path, 'SKILL.md')
            if not os.path.exists(skill_md_path):
                self.stdout.write(self.style.WARNING(f'  跳过 {entry}（无 SKILL.md）'))
                continue

            try:
                with open(skill_md_path, 'r', encoding='utf-8') as f:
                    skill_content = f.read()

                parsed = Skill.parse_skill_md(skill_content)
                skill_name = parsed['name']

                existing = Skill.objects.filter(name=skill_name).first()

                if existing:
                    # 条件：Skill 已激活到不可变版本包；动作：整体跳过（连描述都不改）；
                    # 结果：版本包哈希与库内记录保持一致，派生链路不会凭空失败。
                    full_path = existing.get_full_path()
                    if _is_immutable_package_target(full_path):
                        self.stdout.write(self.style.WARNING(
                            f'  跳过 {skill_name}（已激活到版本包目录，'
                            f'内容更新请走版本发布通道）'
                        ))
                        logger.info(
                            "预置同步跳过版本化 Skill：name=%s skill_id=%s target=%s",
                            skill_name, existing.id, full_path,
                        )
                        continue

                    # 条件：Skill 已存在；动作：更新内容和文件；结果：保留 is_active 等用户运行态配置。
                    existing.description = parsed['description']
                    existing.skill_content = skill_content
                    existing.save(update_fields=['description', 'skill_content', 'updated_at'])

                    if full_path:
                        _sync_files(entry_path, full_path)

                    self.stdout.write(f'  更新 {skill_name}')
                    updated_count += 1
                else:
                    # 条件：Skill 不存在；动作：创建记录并同步文件；结果：新增预置 Skill 生效。
                    skill = Skill.objects.create(
                        project=project,
                        creator=admin_user,
                        name=skill_name,
                        description=parsed['description'],
                        skill_content=skill_content,
                        is_active=True,
                    )

                    storage_path = f'skills/{project.id}/{skill.id}'
                    full_path = os.path.join(settings.MEDIA_ROOT, storage_path)
                    _sync_files(entry_path, full_path)

                    skill.skill_path = storage_path
                    skill.save(update_fields=['skill_path'])

                    self.stdout.write(self.style.SUCCESS(f'  导入 {skill_name}'))
                    created_count += 1

            except Exception as e:
                # 单个 Skill 导入失败不影响后续条目，记录异常后继续处理。
                self.stdout.write(self.style.ERROR(f'  {entry} 失败: {e}'))
                logger.exception('同步 Skill %s 失败', entry)

        self.stdout.write(self.style.SUCCESS(
            f'Skills 同步完成: 新增 {created_count}，更新 {updated_count}'
        ))
