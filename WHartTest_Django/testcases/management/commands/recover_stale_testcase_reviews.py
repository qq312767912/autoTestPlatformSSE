from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from testcases.models import TestCaseReview


class Command(BaseCommand):
    help = "将长时间无进度更新的用例审查任务收尾为失败"

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=15)

    def handle(self, *args, **options):
        minutes = options["minutes"]
        if minutes < 1:
            raise CommandError("--minutes 必须大于 0")

        stale_before = timezone.now() - timedelta(minutes=minutes)
        stale = TestCaseReview.objects.filter(
            status="running",
            updated_at__lt=stale_before,
        )
        count = stale.update(
            status="failed",
            current_step="审查中断",
            error_message=(
                f"任务超过 {minutes} 分钟无进度更新，可能因模型超时或 Worker 重启而中断；"
                "请点击重新审查。"
            ),
            completed_at=timezone.now(),
        )
        self.stdout.write(self.style.SUCCESS(f"已收尾 {count} 条超时用例审查任务"))
