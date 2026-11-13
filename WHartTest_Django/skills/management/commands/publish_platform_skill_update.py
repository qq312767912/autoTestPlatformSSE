"""从明确指定、已审查的源包发布新版本；不信任或覆盖旧版本字节。"""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from api_keys.models import APIKey
from skills.models import Skill, SkillVersion
from skills.packaging import compute_package_sha256
from skills.validation import parse_manifest
from skills.versions import SkillVersionService


class Command(BaseCommand):
    help = '审查已指定源包并创建平台升级版本；默认只读，--apply 发布并切换默认绑定'

    def add_arguments(self, parser):
        parser.add_argument('--skill-id', type=int, required=True)
        parser.add_argument('--source-dir', required=True)
        parser.add_argument('--source-revision', default='')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        skill = Skill.objects.select_related('project', 'creator', 'active_version').get(pk=options['skill_id'])
        source = Path(options['source_dir']).resolve()
        manifest, _ = parse_manifest(source)
        if manifest.get('name') != skill.name:
            raise CommandError('源包与目标 Skill 名称不同，禁止发布')
        fingerprint = compute_package_sha256(source, redact_secrets=True)
        existing = SkillVersion.objects.filter(skill=skill, source_metadata__source_fingerprint=fingerprint).first()
        if existing:
            self.stdout.write(f'源包已发布为 {existing.version}；未修改现有默认绑定')
            return
        old = skill.active_version
        integrity = SkillVersionService.verify_package_integrity(old) if old else None
        self.stdout.write(f'Skill {skill.pk} ({skill.name})；旧包完整性：{bool(integrity and integrity.get("ok"))}；新源包：{fingerprint[:12]}')
        if not options['apply']:
            self.stdout.write('只读检查完成；发布时仍须通过完整安全扫描')
            return
        from skills.platform_skills import is_internal_platform_skill
        key = None
        if is_internal_platform_skill(skill.name):
            key = next((item for item in APIKey.objects.filter(user_id=skill.creator_id, is_active=True).order_by('-created_at') if item.is_valid()), None)
            if key is None:
                raise CommandError('创建者没有有效 API Key，未发布')
        record = {'skill_id': skill.pk, 'old_active_version': str(skill.active_version_id or ''), 'old_path': skill.skill_path,
                  'old_integrity': integrity, 'source_fingerprint': fingerprint, 'source_revision': options['source_revision']}
        journal_dir = Path(settings.MEDIA_ROOT).parent / 'upgrade-backups' / 'platform-skill'
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal = journal_dir / f'{skill.pk}-{timezone.now().strftime("%Y%m%d%H%M%S%f")}.json'
        journal.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        base = (old.version if old else '1.0.0').split('+')[0].split('-')[0]
        version_label, number = base + '-platform-update', 1
        while SkillVersion.objects.filter(skill=skill, version=version_label).exists():
            number += 1
            version_label = base + f'-platform-update.{number}'
        with transaction.atomic():
            locked = Skill.objects.select_for_update().get(pk=skill.pk)
            if locked.active_version_id != skill.active_version_id or locked.skill_path != skill.skill_path:
                raise CommandError('默认版本已变化，停止发布')
            updated, version = SkillVersionService.create_candidate_from_dir(
                source_dir=source, version_override=version_label, project=skill.project, actor=skill.creator,
                api_key=key.key if key else None, source_type='platform_upgrade',
                source_metadata={'source_fingerprint': fingerprint, 'source_revision': options['source_revision'],
                                 'replaces_version': str(old.pk) if old else None, 'previous_package_integrity': bool(integrity and integrity.get('ok'))})
            if updated.pk != skill.pk:
                raise CommandError('发布版本归属异常')
            if not SkillVersionService.verify_package_integrity(version).get('ok'):
                raise CommandError('新包完整性校验失败，未切换默认版本')
            locked.active_version = version
            locked.skill_path = version.package_path
            locked.save(update_fields=['active_version', 'skill_path'])
            record['new_version'] = str(version.pk)
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        self.stdout.write(f'新版本 {version.version} 发布成功；旧包和已有任务版本锁保持不变')
