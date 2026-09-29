"""任务 6：从来源快照批量提炼知识候选。

用法：
    python manage.py extract_knowledge_candidates --project-id <uuid> \
        [--snapshot-id <uuid>] [--dry-run]

说明：
- 未指定 snapshot-id 时，处理该项目下所有 captured/parsed 状态、未提取过的快照。
- 去重键 = project + kind + 内容哈希；重复内容会幂等跳过，内容冲突会生成 KnowledgeConflict。
- 当前使用规则提取器（标题/段落/表格/代码块），LLM 提炼后续替换 extractor 参数即可。
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from knowledge_evolution.extractors import CandidateDeduplicator, RuleBasedExtractor
from knowledge_evolution.knowledge_models import SourceSnapshot
from projects.models import Project


class Command(BaseCommand):
    help = "从来源快照中规则化提炼知识候选"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=str, required=True, help="项目 UUID")
        parser.add_argument("--snapshot-id", type=str, help="仅处理指定快照 UUID")
        parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")

    def handle(self, *args, **options):
        try:
            project = Project.objects.get(pk=options["project_id"])
        except Project.DoesNotExist:
            raise CommandError(f"项目 {options['project_id']} 不存在")

        qs = SourceSnapshot.objects.filter(
            project=project,
            status__in={"captured", "parsed"},
        )
        if options["snapshot_id"]:
            qs = qs.filter(pk=options["snapshot_id"])

        extractor = RuleBasedExtractor()
        dedup = CandidateDeduplicator(project=project, actor=None)

        total_candidates = 0
        total_conflicts = 0
        total_saved = 0

        for snapshot in qs:
            candidates = list(extractor.extract(snapshot))
            unique, conflicts = dedup.dedup_and_conflicts(candidates)
            total_candidates += len(candidates)
            total_conflicts += len(conflicts)

            self.stdout.write(
                f"快照 {snapshot.id} ({snapshot.source_type}:{snapshot.source_id}) "
                f"原始 {len(candidates)} / 去重后 {len(unique)} / 冲突 {len(conflicts)}"
            )

            if options["dry_run"]:
                for c in unique[:5]:
                    self.stdout.write(f"  候选 [{c.kind}/{c.level}] {c.payload.get('title', '')[:40]}")
                continue

            with transaction.atomic():
                saved = dedup.save_candidates(snapshot, unique)
                dedup.save_conflicts(conflicts)
                snapshot.status = "extracted"
                snapshot.save(update_fields=["status", "updated_at"])
                total_saved += len(saved)

        self.stdout.write(
            self.style.SUCCESS(
                f"处理完成：原始候选 {total_candidates}，去重后保存 {total_saved}，冲突 {total_conflicts}"
            )
        )
