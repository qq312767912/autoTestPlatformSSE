"""Read package versions and rewrite only a private, not-yet-imported copy."""
import re
from pathlib import Path
import yaml
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from .canonical import pick_canonical
from .models import Skill, SkillVersion
from .validation import parse_manifest, scan_package_dir

VERSION_PATTERN = r'^[0-9]+(\.[0-9]+){1,3}([-+][A-Za-z0-9.\-]+)?$'


def check_version(value):
    if not isinstance(value, str) or len(value) > 100 or not re.fullmatch(VERSION_PATTERN, value):
        raise ValidationError('版本号必须形如 1.2.0，可带预发布后缀')
    return value


def rewrite_version(root, version):
    version = check_version(version)
    path = Path(root) / 'SKILL.md'
    text = path.read_text(encoding='utf-8-sig')
    match = re.match(r'\A---[^\S\n]*\r?\n(.*?)\r?\n---[^\S\n]*(?:\r?\n|$)', text, re.S)
    if not match:
        raise ValidationError('SKILL.md 缺少有效 YAML frontmatter')
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise ValidationError('SKILL.md frontmatter 必须是对象')
    # Preserve the original body, and update YAML semantically (handles quoted/duplicate keys).
    metadata['version'] = version
    path.write_text('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip() + '\n---\n' + text[match.end():], encoding='utf-8')


def preview_directory(root):
    from .versions import SkillVersionService
    result = []
    for directory in Skill._find_skill_dirs(str(root)):
        manifest, issues = parse_manifest(Path(directory))
        if not manifest.get('name'):
            raise ValidationError('包内 SKILL.md 缺少名称')
        name = manifest['name']
        skill = pick_canonical(list(Skill.objects.filter(name=name).annotate(n_versions=Count('versions')).select_related('active_version'))).get(name)
        declared = str(manifest.get('version') or '')
        versions = list(SkillVersion.objects.filter(skill=skill).values_list('version', flat=True)) if skill else []
        report = scan_package_dir(Path(directory))
        duplicate = SkillVersion.objects.filter(Q(package_sha256=report.package_sha256) | Q(source_metadata__input_package_sha256=report.package_sha256), skill=skill).first() if skill else None
        result.append({'name': name, 'declared_version': declared, 'target_skill_id': skill.pk if skill else None,
            'existing_versions': versions, 'suggested_version': SkillVersionService.next_version(skill),
            'conflict': declared in versions and duplicate is None, 'duplicate_version': duplicate.version if duplicate else '',
            'package_sha256': report.package_sha256})
    if not result:
        raise ValidationError('ZIP 中未找到 SKILL.md')
    return result
