"""升级前后检查 Skill 数据库引用与实际文件是否一致，不写入数据。"""
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from skills.models import Skill, SkillVersion

class Command(BaseCommand):
    help = "只读检查 Skill 文件与版本包；发现缺失时返回非零状态"

    def handle(self, *args, **options):
        missing = []
        for skill in Skill.objects.exclude(skill_path=""):
            if not (Path(skill.get_full_path()) / "SKILL.md").is_file():
                missing.append(f"Skill {skill.pk} ({skill.name}): {skill.skill_path}")
        for version in SkillVersion.objects.exclude(package_path=""):
            if not (Path(version.get_full_path()) / "SKILL.md").is_file():
                missing.append(f"版本 {version.pk} (Skill {version.skill_id}): {version.package_path}")
        for item in missing:
            self.stderr.write(item)
        if missing:
            raise CommandError(f"发现 {len(missing)} 条缺失文件引用；请核对 MEDIA_ROOT、挂载和备份，禁止清理或覆盖版本包")
        self.stdout.write(self.style.SUCCESS("Skill 存储检查通过：所有非空路径均包含 SKILL.md"))
