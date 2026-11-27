"""以新增版本方式升级 UI Skill 的 WS 连接，不改写旧包及任务版本锁。"""
import ast
import json
import shutil
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from skills.models import Skill
from skills.runtime import SkillRuntimeResolver
from skills.versions import SkillVersionService


class Command(BaseCommand):
    help = "检查 UI Skill WS 兼容性；--apply 创建新版本并切换默认绑定，旧包保留"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--exclude-legacy-helper", choices=["weknora_kb.py"], action="append", default=[])
        parser.add_argument("--tool-source", default="/app/bundled_skills/ui-automation-skill/ui_automation_tools.py")

    def handle(self, *args, **options):
        source = Path(options["tool_source"]).read_text()
        function_names = {
            "_get_ws_token", "_ws_url_with_token", "_execute_ws_command",
            "execute_testcase", "execute_page_steps",
        }
        lines = source.splitlines(True)
        template = {
            node.name: ''.join(lines[node.lineno - 1:node.end_lineno]).rstrip()
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name in function_names
        }
        if template.keys() != function_names:
            raise CommandError("升级模板缺少 WS 认证或执行函数")
        records = []
        for skill in Skill.objects.select_related("active_version", "project", "creator").filter(is_active=True):
            baseline = SkillRuntimeResolver.runnable_version(skill)
            root = baseline.get_full_path() if baseline else skill.get_full_path()
            if not root or not Path(root).is_dir():
                continue
            files = list(Path(root).rglob("ui_automation_tools.py"))
            if not files:
                continue
            replacements = {}
            for file in files:
                text = file.read_text()
                legacy_key = 'return (os.environ.get("WHARTTEST_API_KEY") or API_KEY).strip()'
                corrected_key = 'return os.environ.get("WHARTTEST_API_KEY", "PUT_YOUR_WHARTTEST_API_KEY_HERE").strip()'
                if "/ws/ui/web/" not in text:
                    continue
                nodes = {node.name: node for node in ast.parse(text).body
                         if isinstance(node, ast.FunctionDef)}
                if "execute_testcase" not in nodes:
                    raise CommandError(f"Skill {skill.pk} 脚本缺少标准执行入口，请人工适配")
                original = text
                if legacy_key in text:
                    text = text.replace(legacy_key, corrected_key)
                script_lines = text.splitlines(True)
                existing = [node for name, node in nodes.items() if name in template]
                for node in sorted(existing, key=lambda n: n.lineno, reverse=True):
                    script_lines[node.lineno - 1:node.end_lineno] = [template[node.name] + '\n']
                missing = [body for name, body in template.items() if name not in nodes]
                prefix = []
                if "def _base_url(" not in text:
                    prefix.append('def _base_url():\n    import os\n    return (os.environ.get("WHARTTEST_BACKEND_URL") or BASE_URL).rstrip("/")')
                if "def _api_key(" not in text:
                    prefix.append('def _api_key():\n    import os\n    return os.environ.get("WHARTTEST_API_KEY", "").strip()')
                if "_ws_token_cache =" not in text:
                    prefix.append('_ws_token_cache = {"token": "", "expires_at": 0.0}')
                text = ''.join(script_lines)
                if prefix or missing:
                    helper = '\n\n'.join(prefix + missing) + '\n\n'
                    position = text.find('if __name__ == ')
                    if position < 0:
                        text += '\n\n' + helper
                    else:
                        text = text[:position] + helper + text[position:]
                if text == original:
                    continue
                ast.parse(text)
                replacements[file.relative_to(root)] = text
            if not replacements:
                continue
            record = {"skill_id": skill.pk, "old_path": skill.skill_path, "old_active_version": str(skill.active_version_id or ""), "baseline": str(baseline.pk) if baseline else None}
            self.stdout.write(f"Skill {skill.pk} ({skill.name}): {len(replacements)} 个脚本需升级")
            if not options["apply"]:
                continue
            # 回滚指针先落盘，再创建候选；旧包字节始终不变。
            backup = Path(settings.MEDIA_ROOT).parent / "upgrade-backups" / "ws-auth"
            backup.mkdir(parents=True, exist_ok=True)
            journal = backup / f"skill-{skill.pk}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}.json"
            journal.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            with tempfile.TemporaryDirectory() as temporary:
                staged = Path(temporary) / "package"
                shutil.copytree(root, staged)
                omitted = []
                for filename in options["exclude_legacy_helper"]:
                    candidate = staged / filename
                    if candidate.exists():
                        for reference in staged.rglob("*"):
                            if reference.is_file() and reference != candidate and reference.suffix in {".md", ".py", ".json", ".yaml", ".yml"}:
                                if filename.removesuffix(".py") in reference.read_text(errors="ignore"):
                                    raise CommandError(f"{filename} 仍被引用，不允许排除")
                        candidate.unlink()
                        omitted.append(filename)
                record["omitted_unreferenced_legacy_files"] = omitted
                for relative, text in replacements.items():
                    (staged / relative).write_text(text)
                # 旧包可能已注入平台 Key；按平台导出规则还原后扫描，再由服务注入。
                from skills.packaging import redact_text
                for script in staged.rglob("*.py"):
                    text, changed = redact_text(script.read_text())
                    if changed:
                        script.write_text(text)
                api_key = None
                from skills.platform_skills import is_internal_platform_skill
                if is_internal_platform_skill(skill.name):
                    from api_keys.models import APIKey
                    key = next((key for key in APIKey.objects.filter(user_id=skill.creator_id, is_active=True).order_by("-created_at") if key.is_valid()), None)
                    if key is None:
                        raise CommandError(f"Skill {skill.pk} 的创建者缺少有效 API Key，未切换绑定")
                    api_key = key.key
                base_version = (baseline.version if baseline else "1.0.0").split("+")[0].split("-")[0]
                candidate_version = base_version + "-ws-auth"
                from skills.models import SkillVersion
                sequence = 1
                while SkillVersion.objects.filter(skill=skill, version=candidate_version).exists():
                    sequence += 1
                    candidate_version = base_version + f"-ws-auth.{sequence}"
                with transaction.atomic():
                    locked = Skill.objects.select_for_update().get(pk=skill.pk)
                    if locked.active_version_id != skill.active_version_id or locked.skill_path != skill.skill_path:
                        raise CommandError("Skill 绑定已变化，停止升级")
                    updated, version = SkillVersionService.create_candidate_from_dir(source_dir=staged, version_override=candidate_version, project=skill.project, actor=skill.creator, api_key=api_key, source_type="platform_upgrade", source_metadata={"reason": "v2.9.2 WS authentication", "baseline_version": str(baseline.pk) if baseline else None, "omitted_unreferenced_legacy_files": omitted})
                    if updated.pk != skill.pk:
                        raise CommandError("候选版本归属不匹配，停止升级")
                    locked.active_version = version
                    locked.skill_path = version.package_path
                    locked.save(update_fields=["active_version", "skill_path"])
                    record["new_version"] = str(version.pk)
                    journal.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            records.append(record)
        self.stdout.write(f"完成：已升级 {len(records)} 个 Skill" if options["apply"] else "只读检查完成；未修改数据")
