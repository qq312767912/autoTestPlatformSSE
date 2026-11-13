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
        parser.add_argument("--tool-source", default="/app/bundled_skills/ui-automation-skill/ui_automation_tools.py")

    def handle(self, *args, **options):
        source = Path(options["tool_source"]).read_text()
        helper_nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in {"_get_ws_token", "_ws_url_with_token"}]
        if len(helper_nodes) != 2:
            raise CommandError("升级模板缺少 WS 认证函数")
        lines = source.splitlines(True)
        helper = '\n_ws_token_cache = {"token": "", "expires_at": 0.0}\n\n' + '\n'.join(''.join(lines[n.lineno - 1:n.end_lineno]) for n in helper_nodes) + '\n'
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
                if "def _get_ws_token(" in text or "/ws/ui/web/" not in text:
                    continue
                suffix = ".replace('http://', 'ws://').replace('https://', 'wss://') + '/ws/ui/web/'"
                forms = ["_base_url()" + suffix, "BASE_URL" + suffix]
                if not any(form in text for form in forms) or "def execute_testcase(" not in text:
                    raise CommandError(f"Skill {skill.pk} 脚本格式不同，请人工适配；未覆盖")
                for form in forms:
                    text = text.replace(form, "_ws_url_with_token()")
                prefix = ""
                if "def _base_url(" not in text:
                    prefix += '\ndef _base_url():\n    import os\n    return (os.environ.get("WHARTTEST_BACKEND_URL") or BASE_URL).rstrip("/")\n'
                if "def _api_key(" not in text:
                    prefix += '\ndef _api_key():\n    import os\n    return (os.environ.get("WHARTTEST_API_KEY") or API_KEY).strip()\n'
                upgrade_helper = prefix + helper
                text += upgrade_helper
                # 函数查找发生在模块加载后；必须放在 CLI main 调用之前。
                marker = "if __name__ == "
                pos = text.find(marker)
                if pos >= 0:
                    text = text[:pos] + upgrade_helper + '\n' + text[pos:-len(upgrade_helper)]
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
                for relative, text in replacements.items():
                    (staged / relative).write_text(text)
                base_version = baseline.version if baseline else "1.0.0"
                with transaction.atomic():
                    locked = Skill.objects.select_for_update().get(pk=skill.pk)
                    if locked.active_version_id != skill.active_version_id or locked.skill_path != skill.skill_path:
                        raise CommandError("Skill 绑定已变化，停止升级")
                    updated, version = SkillVersionService.create_candidate_from_dir(source_dir=staged, version_override=base_version + "+ws-auth", project=skill.project, actor=skill.creator, source_type="platform_upgrade", source_metadata={"reason": "v2.9.2 WS authentication", "baseline_version": str(baseline.pk) if baseline else None})
                    if updated.pk != skill.pk:
                        raise CommandError("候选版本归属不匹配，停止升级")
                    locked.active_version = version
                    locked.skill_path = version.package_path
                    locked.save(update_fields=["active_version", "skill_path"])
                    record["new_version"] = str(version.pk)
                    journal.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            records.append(record)
        self.stdout.write(f"完成：已升级 {len(records)} 个 Skill" if options["apply"] else "只读检查完成；未修改数据")
