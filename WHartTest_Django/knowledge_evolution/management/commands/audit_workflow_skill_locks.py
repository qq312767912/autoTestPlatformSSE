from django.core.management.base import BaseCommand

from knowledge_evolution.models import GenerationOutput
from knowledge_evolution.workflow_models import WorkflowSkillLock


class Command(BaseCommand):
    help = "只读审计项目流程锁、产出 Skill 版本和包哈希是否一致"

    def add_arguments(self, parser):
        parser.add_argument("--project", type=int, dest="project_id")

    def handle(self, *args, **options):
        locks = WorkflowSkillLock.objects.select_related("skill_version")
        outputs = GenerationOutput.objects.select_related("skill_version")
        if options.get("project_id"):
            locks = locks.filter(project_id=options["project_id"])
            outputs = outputs.filter(project_id=options["project_id"])

        issues = []
        lock_map = {}
        for lock in locks:
            lock_map[(lock.project_id, lock.workflow_id, lock.stage)] = lock
            version = lock.skill_version
            if version is None:
                issues.append(f"lock={lock.pk} 缺少 skill_version")
            elif lock.skill_id and lock.skill_id != version.skill_id:
                issues.append(f"lock={lock.pk} skill 与 skill_version 不一致")
            elif lock.package_sha256 and lock.package_sha256 != version.package_sha256:
                issues.append(f"lock={lock.pk} package_sha256 不一致")

        for output in outputs:
            protocol = (output.metadata or {}).get("protocol") or {}
            workflow_id = str(protocol.get("workflow_id") or "")
            stage = str(protocol.get("stage") or output.task_type or "")
            if not workflow_id:
                continue
            lock = lock_map.get((output.project_id, workflow_id, stage))
            if lock and output.skill_version_id != lock.skill_version_id:
                issues.append(
                    f"output={output.pk} 版本 {output.skill_version_id or '-'} "
                    f"与 lock={lock.pk} 版本 {lock.skill_version_id or '-'} 不一致"
                )

        for issue in issues:
            self.stdout.write(issue)
        self.stdout.write(self.style.SUCCESS(
            f"审计完成：locks={locks.count()} outputs={outputs.count()} issues={len(issues)}"
        ))
        if issues:
            raise SystemExit(1)
