"""Management command: 为指定的来源快照构建文档图谱（tasks.md 任务 8）。

用法示例：
    python manage.py build_document_graph --snapshot <uuid>
    python manage.py build_document_graph --project <id> --all-documents
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from knowledge_evolution.graph import KnowledgeDocumentGraphAdapter
from knowledge_evolution.knowledge_models import SourceSnapshot


class Command(BaseCommand):
    help = "为来源快照构建文档图谱"

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", type=str, help="SourceSnapshot UUID")
        parser.add_argument("--project", type=int, help="项目 ID")
        parser.add_argument(
            "--all-documents", action="store_true",
            help="为项目下全部 document 类型快照建图（需配合 --project）",
        )
        parser.add_argument("--projection", type=str, help="关联的 IndexProjection UUID")
        parser.add_argument(
            "--include-versions", action="store_true",
            help="同时把关联的 KnowledgeVersion / Candidate 写入图谱",
        )

    def handle(self, *args, **options):
        adapter = KnowledgeDocumentGraphAdapter()
        if options["snapshot"]:
            snapshot = SourceSnapshot.objects.get(pk=options["snapshot"])
            if options["include_versions"]:
                result = adapter.build_full_for_snapshot(snapshot, options["projection"])
            else:
                result = adapter.build_from_snapshot(snapshot, options["projection"])
            self.stdout.write(self.style.SUCCESS(result))
            return

        if options["all_documents"] and options["project"]:
            snapshots = SourceSnapshot.objects.filter(
                project_id=options["project"], source_type="document"
            )
            total_nodes = 0
            total_edges = 0
            for snapshot in snapshots:
                r = adapter.build_from_snapshot(snapshot, options["projection"])
                total_nodes += r["node_count"]
                total_edges += r["edge_count"]
            self.stdout.write(self.style.SUCCESS(
                f"已处理 {snapshots.count()} 个文档快照，"
                f"节点 {total_nodes}，边 {total_edges}"
            ))
            return

        self.stdout.write(self.style.ERROR("必须指定 --snapshot 或 --project + --all-documents"))
