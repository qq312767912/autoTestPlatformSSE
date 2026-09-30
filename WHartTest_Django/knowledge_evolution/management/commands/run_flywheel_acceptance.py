import json

from django.core.management.base import BaseCommand, CommandError

from knowledge_evolution.operations import FlywheelMetricsService, KnowledgeHealthService
from projects.models import Project


class Command(BaseCommand):
    help = "执行飞轮验收快照，输出质量、漏报/误报、Token、耗时和发布状态"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=int, required=True)
        parser.add_argument("--require-task-types", default="knowledge_query,code_review,testcase_generation,test_execution")

    def handle(self, *args, **options):
        try:
            project = Project.objects.get(pk=options["project_id"])
        except Project.DoesNotExist as exc:
            raise CommandError("项目不存在") from exc
        metrics = FlywheelMetricsService().summarize(project.id)
        health = KnowledgeHealthService().inspect(project.id)
        required = [item.strip() for item in options["require_task_types"].split(",") if item.strip()]
        from knowledge_evolution.models import RetrievalTrace
        coverage = {
            task_type: RetrievalTrace.objects.filter(project=project, task_type=task_type).exists()
            for task_type in required
        }
        report = {"project": project.name, "coverage": coverage, "metrics": metrics, "health": health}
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if not all(coverage.values()):
            self.stderr.write(self.style.WARNING("部分验收场景没有真实轨迹，报告已明确标记，不以模拟数据冒充"))
        else:
            self.stdout.write(self.style.SUCCESS("飞轮真实场景覆盖检查通过"))
