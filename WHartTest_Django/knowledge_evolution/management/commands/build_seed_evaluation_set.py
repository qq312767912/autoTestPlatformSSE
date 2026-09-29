"""任务 3：从现有任务中抽取种子评测集。

用法：
    python manage.py build_seed_evaluation_set \
        --project-id <uuid> \
        --name "Phase0 种子集" \
        --target-size 50

逻辑：
1. 优先复用已有飞轮记录（code_review / test_execution / knowledge_query）。
2. 若真实样本不足，用模板补齐到 --target-size。
3. 按 split_ratio 分配 gold/regression/fresh/challenge，默认 2:5:2:1。
4. 去重键 = suite + source_task_id + case_key；重复运行幂等。
"""

import json
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from knowledge_evolution.models import (
    EvaluationCase,
    EvaluationSuite,
    GenerationOutput,
    RetrievalTrace,
)
from projects.models import Project


SPLIT_RATIO_DEFAULT = {"gold": 0.2, "regression": 0.5, "fresh": 0.2, "challenge": 0.1}


def assign_split(index: int) -> str:
    """按 2:5:2:1 把第 index 条样本映射到 split。"""
    bucket = index % 10
    if bucket < 2:
        return "gold"
    if bucket < 7:
        return "regression"
    if bucket < 9:
        return "fresh"
    return "challenge"


class Command(BaseCommand):
    help = "从现有飞轮记录中抽取种子评测集"

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=str, required=True, help="项目 UUID")
        parser.add_argument("--name", type=str, default="Phase0 种子集", help="评测集名称")
        parser.add_argument("--target-size", type=int, default=50, help="目标样本数")
        parser.add_argument(
            "--task-types", type=str, default="code_review,test_execution,knowledge_query,testcase_generation",
            help="逗号分隔的任务类型"
        )
        parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写库")

    def handle(self, *args, **options):
        project_id = options["project_id"]
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise CommandError(f"项目 {project_id} 不存在")

        target_size = max(1, options["target_size"])
        task_types = [t.strip() for t in options["task_types"].split(",") if t.strip()]

        suite, _ = EvaluationSuite.objects.get_or_create(
            project=project,
            name=options["name"],
            suite_type="seed",
            defaults={
                "task_type": task_types[0],
                "description": "Phase 0 可观测基线种子集：从真实任务与模板补齐生成",
                "split_ratio": SPLIT_RATIO_DEFAULT,
                "is_active": True,
            },
        )

        existing_keys = set(
            EvaluationCase.objects.filter(suite=suite).values_list("metadata__seed_key", flat=True)
        )
        cases = []

        # 1) 从真实记录抽取
        real_cases = self._extract_real_cases(suite, project, task_types, existing_keys)
        cases.extend(real_cases)
        self.stdout.write(self.style.SUCCESS(f"从真实记录抽取 {len(real_cases)} 条"))

        # 2) 用模板补齐到 target_size
        if len(cases) < target_size:
            synthetic = self._build_synthetic_cases(
                suite, project, task_types, target_size - len(cases), existing_keys,
                start_index=len(cases),
            )
            cases.extend(synthetic)
            self.stdout.write(self.style.WARNING(f"用模板补齐 {len(synthetic)} 条"))

        if not options["dry_run"]:
            with transaction.atomic():
                EvaluationCase.objects.bulk_create(cases, ignore_conflicts=True)

        split_counts = defaultdict(int)
        for c in cases:
            split_counts[c.split] += 1

        self.stdout.write(self.style.SUCCESS(
            f"评测集 '{suite.name}' 新增/复用 {len(cases)} 条，split 分布：{dict(split_counts)}"
        ))

    def _extract_real_cases(self, suite, project, task_types, existing_keys):
        cases = []
        traces = RetrievalTrace.objects.filter(
            project=project, task_type__in=task_types
        ).select_related("project").prefetch_related("outputs")[:200]

        for trace in traces:
            output = GenerationOutput.objects.filter(trace=trace).first()
            task_id = trace.task_id or str(trace.id)[:8]
            key = f"{trace.task_type}:{task_id}"
            if key in existing_keys:
                continue
            expected = self._build_expected(trace, output)
            cases.append(EvaluationCase(
                suite=suite,
                case_number=len(cases) + 1,
                task_type=trace.task_type,
                input_payload={
                    "query": trace.query,
                    "task_id": task_id,
                    "channels": trace.channels,
                },
                expected_payload=expected,
                source_trace=trace,
                source_output=output,
                golden_labels=self._infer_labels(trace, output),
                split=assign_split(len(cases)),
                annotator="seed-extractor",
                annotated_at=timezone.now(),
                metadata={"seed_key": key, "origin": "real"},
            ))
        return cases

    def _build_expected(self, trace, output):
        expected = {"candidates_count": len(trace.candidates or [])}
        if output:
            expected["output_length"] = len(output.content)
        return expected

    def _infer_labels(self, trace, output):
        labels = {"is_valid": True}
        if trace.task_type == "test_execution":
            labels["should_be_test_passed"] = "test_passed" in (output.content or "") if output else False
        return labels

    def _build_synthetic_cases(self, suite, project, task_types, needed, existing_keys, start_index):
        templates = [
            {
                "task_type": "knowledge_query",
                "query": "如何配置测试环境的域名解析？",
                "expected": {"must_cite": ["docker-compose", "extra_hosts"], "answer_contains": "extra_hosts"},
                "labels": {"is_valid": True, "domain": "ops"},
            },
            {
                "task_type": "code_review",
                "query": "review: main.py",
                "expected": {"findings_count": 2, "must_dispositions": ["confirmed", "needs_confirmation"]},
                "labels": {"is_valid": True, "has_confirmed_defect": False},
            },
            {
                "task_type": "test_execution",
                "query": "执行回归套件 A",
                "expected": {"status": "completed", "pass_rate": 1.0},
                "labels": {"is_valid": True, "should_be_test_passed": True},
            },
            {
                "task_type": "testcase_generation",
                "query": "根据登录需求生成测试用例",
                "expected": {"case_count": 5, "must_cover": ["正常登录", "密码错误"]},
                "labels": {"is_valid": True, "coverage_complete": False},
            },
        ]
        cases = []
        idx = start_index
        while len(cases) < needed:
            tmpl = templates[idx % len(templates)]
            if tmpl["task_type"] not in task_types:
                idx += 1
                continue
            key = f"synthetic:{tmpl['task_type']}:{idx}"
            if key in existing_keys:
                idx += 1
                continue
            cases.append(EvaluationCase(
                suite=suite,
                case_number=idx + 1,
                task_type=tmpl["task_type"],
                input_payload={"query": tmpl["query"]},
                expected_payload=tmpl["expected"],
                source_trace=None,
                source_output=None,
                golden_labels=tmpl["labels"],
                split=assign_split(idx),
                annotator="seed-template",
                annotated_at=timezone.now(),
                metadata={"seed_key": key, "origin": "synthetic"},
            ))
            idx += 1
        return cases
