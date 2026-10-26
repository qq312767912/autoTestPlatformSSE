"""代码审查测评：自动匹配只产生候选，正式指标由人工核验产生。"""

from collections import defaultdict

from celery import shared_task
from code_analysis.models import AnalysisTask
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .evaluation_models import EvaluationRun, EvaluationResult

TERMINAL = {"completed", "degraded", "partial", "failed", "cancelled"}
SUCCESS = {"completed", "degraded"}


def _normalized(value):
    return " ".join(str(value or "").lower().split())


def _matches(finding, issue):
    file_name = _normalized(issue.get("file") or issue.get("path"))
    if file_name and _normalized(finding.get("file")) != file_name:
        return False
    line = issue.get("line")
    finding_line = finding.get("line") or finding.get("start_line")
    if line and finding_line:
        try:
            if abs(int(line) - int(finding_line)) > 5:
                return False
        except (TypeError, ValueError):
            return False
    expected = set(_normalized(issue.get("description")).split())
    actual = set(_normalized(" ".join(str(finding.get(key) or "") for key in ("change", "evidence", "trigger", "impact"))).split())
    return bool(expected and actual and len(expected & actual) / len(expected) >= 0.6)


def _defects(task):
    return [item for item in ((task.change_report or {}).get("findings") or [])
            if isinstance(item, dict) and item.get("disposition") == "confirmed"]


def _all_findings(task):
    """全部机器/AI 发现（含待确认）。

    `_defects()` 只取 confirmed，用于严格口径的「已确认缺陷数」；
    但报告要能区分「没发现问题」和「报了问题但都待确认」，所以另计原始数量。
    """
    return [item for item in ((task.change_report or {}).get("findings") or []) if isinstance(item, dict)]


def _duration_ms(task):
    if not task or not task.completed_at:
        return None
    return max(0, int((task.completed_at - task.created_at).total_seconds() * 1000))


def _provenance(metadata):
    """样本来源标签：区分内置公开集、内部集与历史手工「伪公开」导入。"""
    source = str((metadata or {}).get("source") or "")
    if source == "benchmark":
        return "benchmark_catalog"
    if source == "public":
        return "legacy_custom"
    return source or "internal"


def _bucket():
    return {"cases": 0, "completed": 0, "failed": 0, "clean_cases": 0,
            "gold_issues": 0, "reported_findings": 0, "raw_findings": 0, "candidate_matches": 0,
            "verified_cases": 0, "verified_hits": 0, "verified_valid_findings": 0,
            "verified_false_findings": 0, "verified_clean_false_cases": 0,
            "tokens": 0, "duration_ms": 0, "duration_samples": 0}


def _rates(bucket):
    fully_verified = bucket["completed"] > 0 and bucket["verified_cases"] == bucket["completed"]
    bucket["candidate_recall"] = round(bucket["candidate_matches"] / bucket["gold_issues"], 4) if bucket["gold_issues"] else None
    bucket["verified_recall"] = round(bucket["verified_hits"] / bucket["gold_issues"], 4) if fully_verified and bucket["gold_issues"] else None
    bucket["verified_precision"] = round(bucket["verified_valid_findings"] / bucket["reported_findings"], 4) if fully_verified and bucket["reported_findings"] else None
    bucket["clean_pr_false_positive_rate"] = round(bucket["verified_clean_false_cases"] / bucket["clean_cases"], 4) if fully_verified and bucket["clean_cases"] else None
    bucket["avg_tokens"] = round(bucket["tokens"] / bucket["completed"]) if bucket["completed"] else None
    bucket["avg_duration_ms"] = round(bucket["duration_ms"] / bucket["duration_samples"]) if bucket["duration_samples"] else None
    return bucket


def build_report(run):
    rows = list(run.results.select_related("case").order_by("case__case_number"))
    tasks = {str(task.id): task for task in AnalysisTask.objects.filter(
        id__in=[row.predicted_payload.get("analysis_task_id") for row in rows], project=run.suite.project)}
    totals, languages, details = _bucket(), defaultdict(_bucket), []
    for row in rows:
        task = tasks.get(str(row.predicted_payload.get("analysis_task_id")))
        status = task.status if task else "missing"
        issues = list((row.case.golden_labels or {}).get("issues") or [])
        findings = _defects(task) if task else []
        all_findings = _all_findings(task) if task else []
        language = str((row.case.metadata or {}).get("language") or "unknown").lower()
        language = "c++" if language == "cpp" else language
        candidates, used = [], set()
        for gold_index, issue in enumerate(issues):
            finding_index = next((index for index, finding in enumerate(findings)
                                  if index not in used and _matches(finding, issue)), None)
            if finding_index is not None:
                used.add(finding_index)
                candidates.append({"gold_index": gold_index, "finding_index": finding_index})
        adjudication = (row.raw_scores or {}).get("code_review_adjudication") or {}
        verified = bool(adjudication.get("completed")) and status in SUCCESS
        matched = adjudication.get("matches") or []
        false_findings = adjudication.get("false_findings") or []
        valid_findings = adjudication.get("valid_findings") or []
        duration = _duration_ms(task)
        for bucket in (totals, languages[language]):
            bucket["cases"] += 1
            if status in SUCCESS:
                bucket["completed"] += 1
                bucket["clean_cases"] += int(not issues)
                bucket["gold_issues"] += len(issues)
                bucket["reported_findings"] += len(findings)
                bucket["raw_findings"] += len(all_findings)
                bucket["candidate_matches"] += len(candidates)
                bucket["tokens"] += task.token_usage
                if duration is not None:
                    bucket["duration_ms"] += duration
                    bucket["duration_samples"] += 1
                if verified:
                    bucket["verified_cases"] += 1
                    bucket["verified_hits"] += len(matched)
                    bucket["verified_valid_findings"] += len(matched) + len(valid_findings)
                    bucket["verified_false_findings"] += len(false_findings)
                    bucket["verified_clean_false_cases"] += int(not issues and bool(false_findings))
            elif status in TERMINAL or status == "missing":
                bucket["failed"] += 1
        details.append({"case_number": row.case.case_number, "result_id": str(row.id),
                        "task_id": str(task.id) if task else None, "status": status,
                        "language": language, "source_url": (row.case.metadata or {}).get("source_url", ""),
                        "provenance": _provenance(row.case.metadata or {}),
                        "base_sha": row.case.input_payload.get("base_sha"), "head_sha": row.case.input_payload.get("head_sha"),
                        "issues": issues, "findings": findings, "raw_findings": len(all_findings),
                        "candidate_matches": candidates,
                        "adjudication": adjudication, "tokens": task.token_usage if task else 0,
                        "duration_ms": duration, "error": task.error_message if task else "审查任务不存在"})
    all_done = bool(rows) and all(item["status"] in TERMINAL for item in details)
    return {"run_id": str(run.id), "suite": run.suite.name,
            "status": "completed" if all_done else "running", "config": run.config,
            "started_at": run.started_at, "finished_at": run.finished_at,
            "summary": _rates(totals), "languages": {key: _rates(value) for key, value in sorted(languages.items())},
            "note": "自动匹配仅为候选；正式指标须完成逐样本人工核验。失败样本不计入质量分母。",
            "cases": details}


def adjudicate_case(run, payload, actor):
    try:
        result = EvaluationResult.objects.select_related("case").filter(run=run, id=payload.get("result_id")).first()
    except (TypeError, ValueError):
        raise ValidationError({"result_id": "样本编号格式错误"})
    if not result:
        raise ValidationError({"result_id": "该样本不属于当前运行"})
    task = AnalysisTask.objects.filter(id=result.predicted_payload.get("analysis_task_id"), project=run.suite.project).first()
    if not task or task.status not in SUCCESS:
        raise ValidationError({"detail": "仅能核验已成功完成的审查任务"})
    issues = list((result.case.golden_labels or {}).get("issues") or [])
    findings = _defects(task)
    matches, false_findings, valid_findings = (payload.get(key) for key in ("matches", "false_findings", "valid_findings"))
    if not all(isinstance(value, list) for value in (matches, false_findings, valid_findings)):
        raise ValidationError({"detail": "须提供 matches、false_findings、valid_findings 数组"})
    try:
        pairs = [(int(item["gold_index"]), int(item["finding_index"])) for item in matches]
        false_set, valid_set = {int(value) for value in false_findings}, {int(value) for value in valid_findings}
    except (KeyError, TypeError, ValueError):
        raise ValidationError({"detail": "核验索引格式错误"})
    matched_gold, matched_findings = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    if len(set(matched_gold)) != len(pairs) or len(set(matched_findings)) != len(pairs):
        raise ValidationError({"matches": "一个金标或发现不能重复匹配"})
    if any(gold < 0 or gold >= len(issues) or finding < 0 or finding >= len(findings) for gold, finding in pairs):
        raise ValidationError({"matches": "匹配索引越界"})
    if false_set & valid_set or (false_set | valid_set) & set(matched_findings):
        raise ValidationError({"detail": "同一发现不能同时匹配、有效和误报"})
    if false_set | valid_set | set(matched_findings) != set(range(len(findings))):
        raise ValidationError({"detail": "每条确定缺陷都必须完成判定"})
    if any(index < 0 or index >= len(findings) for index in false_set | valid_set):
        raise ValidationError({"detail": "发现索引越界"})
    raw = dict(result.raw_scores or {})
    raw["code_review_adjudication"] = {"completed": True,
        "matches": [{"gold_index": gold, "finding_index": finding} for gold, finding in pairs],
        "false_findings": sorted(false_set), "valid_findings": sorted(valid_set),
        "reviewer": str(actor), "reviewed_at": timezone.now().isoformat(),
        "finding_count": len(findings), "gold_count": len(issues)}
    result.raw_scores = raw
    result.save(update_fields=["raw_scores", "updated_at"])


@shared_task(bind=True, max_retries=1000)
def finalize_code_review_benchmark(self, run_id):
    run = EvaluationRun.objects.select_related("suite").get(pk=run_id)
    report = build_report(run)
    if report["status"] != "completed":
        raise self.retry(countdown=20)
    tasks = {str(task.id): task for task in AnalysisTask.objects.filter(
        id__in=[case["task_id"] for case in report["cases"] if case["task_id"]])}
    for result in run.results.all():
        task = tasks.get(str(result.predicted_payload.get("analysis_task_id")))
        result.status = "completed" if task and task.status in SUCCESS else "failed"
        result.token_usage = task.token_usage if task else 0
        result.latency_ms = _duration_ms(task) or 0
        result.predicted_payload = {"analysis_task_id": str(task.id), "findings": (task.change_report or {}).get("findings", [])} if task else result.predicted_payload
        result.error_message = task.error_message if task else "审查任务不存在"
        result.save(update_fields=["status", "token_usage", "latency_ms", "predicted_payload", "error_message", "updated_at"])
    run.status = "completed" if not report["summary"]["failed"] else "failed"
    run.finished_at = timezone.now()
    run.metrics_summary = report["summary"]
    run.cost_summary = {"tokens": report["summary"]["tokens"]}
    run.save(update_fields=["status", "finished_at", "metrics_summary", "cost_summary", "updated_at"])
    binding = getattr(run, "benchmark_binding", None)
    if binding is not None and binding.status != run.status:
        binding.status = run.status
        binding.save(update_fields=["status"])
    return report["summary"]
