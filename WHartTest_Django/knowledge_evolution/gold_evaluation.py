"""将冻结金标版本稳定投影为现有EvaluationSuite/EvaluationCase。"""
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import EvaluationCase, EvaluationSuite


class GoldEvaluationBridge:
    @staticmethod
    @transaction.atomic
    def materialize(*, version, actor=None):
        if version.state != "frozen" or not version.content_hash:
            raise ValidationError("只有已冻结且具有内容哈希的金标版本可以进入评测")
        dataset = version.dataset
        suite, _ = EvaluationSuite.objects.get_or_create(
            project=dataset.project,
            name=f"{dataset.name}@{version.version}",
            suite_type="gold",
            defaults={
                "task_type": dataset.task_type,
                "description": f"冻结金标版本 {version.id} / {version.content_hash}",
                "split_ratio": version.sample_stats.get("splits", {}),
                "created_by": actor,
            },
        )
        for number, gold_case in enumerate(version.cases.order_by("id"), start=1):
            defaults = {
                "task_type": gold_case.task_type,
                "input_payload": {
                    **gold_case.input_snapshot,
                    "gold_case_id": str(gold_case.id),
                    "gold_version_hash": version.content_hash,
                },
                "expected_payload": gold_case.expected_output,
                "source_trace": gold_case.source_output.trace if gold_case.source_output_id else None,
                "source_output": gold_case.source_output,
                "golden_labels": {
                    "must_contain": gold_case.required_items,
                    "must_not_contain": gold_case.forbidden_items,
                    "rubric": gold_case.rubric,
                },
                "split": gold_case.split,
                "annotator": "gold-dataset-v2",
                "metadata": {
                    "gold_case_id": str(gold_case.id),
                    "gold_dataset_version_id": str(version.id),
                    "gold_version_hash": version.content_hash,
                    "privacy_level": gold_case.privacy_level,
                    "allow_optimization": gold_case.allow_optimization,
                },
            }
            case, created = EvaluationCase.objects.get_or_create(
                suite=suite, case_number=number, defaults=defaults,
            )
            if not created and case.metadata.get("gold_version_hash") != version.content_hash:
                raise ValidationError("评测集已存在但金标哈希不一致")
        return suite
