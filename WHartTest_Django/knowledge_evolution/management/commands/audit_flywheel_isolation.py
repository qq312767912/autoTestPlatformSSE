from django.core.management.base import BaseCommand
from django.db.models import F, Q

from knowledge_evolution.history_models import HistoryImportItem, HistoryReplay


class Command(BaseCommand):
    help = "审计历史包、回放、金标和文件的跨项目引用；只报告，不自动修改。"

    def add_arguments(self, parser):
        parser.add_argument("--project", type=int)
        parser.add_argument("--fail-on-error", action="store_true")

    def handle(self, *args, **options):
        items = HistoryImportItem.objects.select_related("batch", "file")
        replays = HistoryReplay.objects.select_related(
            "project", "batch", "flywheel_run", "gold_version__dataset",
        )
        if options.get("project"):
            items = items.filter(batch__project_id=options["project"])
            replays = replays.filter(project_id=options["project"])
        bad_items = list(items.exclude(batch__project_id=F("file__project_id")).values_list("pk", flat=True))
        bad_replays = list(replays.filter(
            ~Q(batch__project_id=F("project_id")) |
            ~Q(flywheel_run__project_id=F("project_id")) |
            (Q(gold_version__isnull=False) & ~Q(gold_version__dataset__project_id=F("project_id")))
        ).values_list("pk", flat=True))
        total = len(bad_items) + len(bad_replays)
        self.stdout.write(f"history_item_cross_project={len(bad_items)} history_replay_cross_project={len(bad_replays)}")
        if bad_items:
            self.stdout.write("item_ids=" + ",".join(map(str, bad_items)))
        if bad_replays:
            self.stdout.write("replay_ids=" + ",".join(map(str, bad_replays)))
        if total and options.get("fail_on_error"):
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("审计完成" if not total else "审计完成，发现异常"))
