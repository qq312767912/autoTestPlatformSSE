import fnmatch
import json
import re
from urllib.parse import quote

import requests
from django.utils import timezone

from .models import AnalysisTask, TestRequirementDraft, UserGitLabCredential


DEFAULT_ANNOTATIONS = {
    "Excel", "ExcelProperty", "JsonProperty", "JSONField", "NotNull", "NotBlank",
    "Size", "Transactional", "PreAuthorize", "RequestMapping", "GetMapping", "PostMapping",
}
CRITICAL_MODIFIERS = {"public", "protected", "private", "static", "final", "synchronized", "volatile", "abstract"}


class AnalysisCancelled(Exception):
    pass


def _ensure_not_cancelled(task):
    task.refresh_from_db(fields=["status"])
    if task.status == "cancelled":
        raise AnalysisCancelled("用户已取消分析")


class GitLabClient:
    def __init__(self, connection, token):
        self.base_url = connection.base_url.rstrip("/")
        self.verify = connection.verify_ssl
        self.headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}

    def get(self, path, params=None):
        response = requests.get(f"{self.base_url}/api/v4{path}", headers=self.headers, params=params, timeout=45, verify=self.verify)
        response.raise_for_status()
        return response.json()

    def project(self, project_id):
        return self.get(f"/projects/{quote(str(project_id), safe='')}")

    def merge_requests(self, project_id):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/merge_requests", {"state": "opened", "per_page": 100})

    def merge_request_changes(self, project_id, iid):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/merge_requests/{iid}/changes")

    def compare(self, project_id, base_sha, head_sha):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/repository/compare", {"from": base_sha, "to": head_sha, "straight": True})


def _parse_diff(diff_text, file_path, annotations):
    findings = []
    deleted = [line[1:].strip() for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")]
    for line in deleted:
        annotation = re.match(r"@([A-Za-z_$][\w$]*)", line)
        if annotation and annotation.group(1) in annotations:
            name = annotation.group(1)
            findings.append({
                "key": f"annotation:{file_path}:{name}:{len(findings)}", "change": f"删除 @{name} 注解",
                "file": file_path, "severity": "high", "source": "machine_rule", "confidence": 1.0,
                "evidence": line, "impact": "字段映射、接口契约、权限、事务或校验行为可能发生变化",
            })
        modifier_tokens = set(re.findall(r"\b(public|protected|private|static|final|synchronized|volatile|abstract)\b", line))
        if modifier_tokens & CRITICAL_MODIFIERS and re.search(r"\b(class|interface|enum|\w+\s*\()", line):
            findings.append({
                "key": f"modifier:{file_path}:{len(findings)}", "change": f"删除包含修饰符的声明：{line[:120]}",
                "file": file_path, "severity": "medium", "source": "machine_rule", "confidence": 0.9,
                "evidence": line, "impact": "可见性、共享状态、不可变性或并发语义可能变化",
            })
    return findings


def _diff_line_stats(diff_text):
    additions = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return additions, deletions


def _test_point_for(finding):
    evidence = finding.get("evidence", "")
    if "@Excel" in evidence or "@ExcelProperty" in evidence:
        return {"title": "验证Excel导入导出字段完整性", "objective": "确认注解变更未导致列缺失、表头或顺序异常", "expected_result": "导入导出字段、表头、顺序和数据与需求一致", "priority": "high", "test_type": "功能回归"}
    return {"title": f"验证{finding['change']}的业务兼容性", "objective": finding.get("impact", "验证代码变更影响"), "expected_result": "相关功能、接口及异常分支行为符合需求", "priority": finding.get("severity", "medium"), "test_type": "变更回归"}


def _json_from_response(content):
    text = content if isinstance(content, str) else str(content)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM未返回JSON对象")
    return json.loads(text[start:end + 1])


def _run_ai_batches(task, analyzable_diffs, machine_findings):
    """只向模型发送受控的小批量Diff；任何失败均降级为机器结果。"""
    if task.mode == "quick" or not analyzable_diffs:
        return [], [], 0, 0, "快速模式不调用LLM"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance

    try:
        config = LLMConfig.objects.get(is_active=True)
    except (LLMConfig.DoesNotExist, LLMConfig.MultipleObjectsReturned):
        return [], [], 0, 0, "没有唯一启用的LLM配置，已仅生成机器分析结果"

    max_files = 24 if task.mode == "standard" else 60
    max_chars = 5000 if task.mode == "standard" else 9000
    batches = []
    current, current_size = [], 0
    for item in analyzable_diffs[:max_files]:
        path = item["path"]
        snippet = item["diff"][:max_chars]
        block = f"FILE: {path}\n{snippet}"
        if current and current_size + len(block) > 14000:
            batches.append("\n\n".join(current)); current, current_size = [], 0
        current.append(block); current_size += len(block)
    if current:
        batches.append("\n\n".join(current))

    llm = create_llm_instance(config, temperature=0.1)
    ai_findings, test_points, used = [], [], 0
    prompt_head = (
        "你是测试代码审查助手。机器规则结论不可删除。只分析给出的局部 Diff，不推测未提供源码。"
        "输出严格JSON：{\"risks\":[{\"change\":\"\",\"file\":\"\",\"severity\":\"high|medium|low\","
        "\"confidence\":0.0,\"evidence\":\"\",\"impact\":\"\"}],\"test_requirements\":[{\"title\":\"\","
        "\"objective\":\"\",\"expected_result\":\"\",\"priority\":\"high|medium|low\",\"test_type\":\"\"}]}。"
    )
    for index, batch in enumerate(batches):
        known = [x for x in machine_findings if x.get("file") in batch]
        response = llm.invoke(f"{prompt_head}\n机器已确认风险：{json.dumps(known, ensure_ascii=False)}\nDIFF:\n{batch}")
        payload = _json_from_response(getattr(response, "content", response))
        for risk in payload.get("risks", []):
            risk.update({"key": f"ai:{index}:{len(ai_findings)}", "source": "ai_analysis"})
            ai_findings.append(risk)
        test_points.extend(payload.get("test_requirements", []))
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        used += int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)
    coverage = round(min(len(analyzable_diffs), max_files) / len(analyzable_diffs) * 100, 1)
    note = "AI已按文件分批分析" if coverage == 100 else f"AI仅分析前{max_files}个文件，其余保留机器分析"
    return ai_findings, test_points, used, coverage, note


def run_analysis(task: AnalysisTask):
    _ensure_not_cancelled(task)
    task.status, task.progress, task.current_step = "fetching", 10, "读取GitLab代码变更"
    task.save(update_fields=["status", "progress", "current_step", "updated_at"])
    try:
        credential = UserGitLabCredential.objects.get(project=task.project, connection=task.repository.connection, user=task.creator)
        client = GitLabClient(task.repository.connection, credential.get_token())
        if task.source_type == "merge_request":
            payload = client.merge_request_changes(task.repository.gitlab_project_id, task.merge_request_iid)
            task.base_sha = payload.get("diff_refs", {}).get("base_sha", task.base_sha)
            task.head_sha = payload.get("diff_refs", {}).get("head_sha", task.head_sha)
            task.title = task.title or payload.get("title", "")
            diffs = payload.get("changes", [])
        else:
            payload = client.compare(task.repository.gitlab_project_id, task.base_sha, task.head_sha)
            diffs = payload.get("diffs", [])

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "machine_analyzing", 40, "检测关键代码变化"
        task.save(update_fields=["status", "progress", "current_step", "base_sha", "head_sha", "title", "updated_at"])
        patterns = task.repository.excluded_patterns or []
        annotations = DEFAULT_ANNOTATIONS | set(task.repository.critical_annotations or [])
        files, findings, raw_parts, analyzable_diffs = [], [], [], []
        total_additions, total_deletions = 0, 0
        for item in diffs:
            path = item.get("new_path") or item.get("old_path") or ""
            excluded = any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
            diff = item.get("diff", "")
            additions, deletions = _diff_line_stats(diff)
            total_additions += additions
            total_deletions += deletions
            files.append({"path": path, "old_path": item.get("old_path"), "new_file": item.get("new_file", False), "deleted_file": item.get("deleted_file", False), "excluded": excluded, "additions": additions, "deletions": deletions, "changed_lines": additions + deletions})
            if excluded and not item.get("deleted_file"):
                continue
            raw_parts.append(f"diff -- {path}\n{diff}")
            analyzable_diffs.append({"path": path, "diff": diff})
            findings.extend(_parse_diff(diff, path, annotations))
            if item.get("deleted_file"):
                findings.append({"key": f"file_deleted:{path}", "change": "删除文件", "file": path, "severity": "medium", "source": "machine_rule", "confidence": 1.0, "evidence": path, "impact": "依赖该文件的功能可能受到影响"})

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "ai_analyzing", 65, "分批分析语义风险"
        task.save(update_fields=["status", "progress", "current_step", "updated_at"])
        ai_failed = False
        try:
            ai_findings, ai_test_points, token_usage, ai_coverage, ai_note = _run_ai_batches(task, analyzable_diffs, findings)
            findings.extend(ai_findings)
        except Exception as exc:
            ai_findings, ai_test_points, token_usage, ai_coverage = [], [], 0, 0
            ai_note, ai_failed = f"AI分析失败，已保留机器结果：{exc}", True

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "generating_tests", 82, "生成测试分析报告"
        task.save(update_fields=["status", "progress", "current_step", "updated_at"])
        task.raw_diff = "\n\n".join(raw_parts)
        task.change_report = {
            "summary": {"changed_files": len(files), "additions": total_additions, "deletions": total_deletions, "changed_lines": total_additions + total_deletions, "risk_count": len(findings), "high_risk_count": sum(1 for x in findings if x["severity"] == "high")},
            "files": files, "findings": findings,
            "impact_summary": sorted({x["impact"] for x in findings}),
            "analysis_note": ai_note,
        }
        drafts = []
        for finding in findings:
            point = _test_point_for(finding)
            draft = TestRequirementDraft.objects.create(task=task, source_finding_key=finding["key"], **point)
            drafts.append({"id": draft.id, **point, "source_finding_key": finding["key"], "status": draft.status})
        for index, point in enumerate(ai_test_points):
            safe_point = {
                "title": point.get("title", "代码变更回归验证"),
                "objective": point.get("objective", "验证代码变更影响"),
                "expected_result": point.get("expected_result", "相关功能符合需求"),
                "priority": point.get("priority", "medium"),
                "test_type": point.get("test_type", "AI风险回归"),
            }
            draft = TestRequirementDraft.objects.create(task=task, source_finding_key=f"ai-test:{index}", **safe_point)
            drafts.append({"id": draft.id, **safe_point, "source_finding_key": f"ai-test:{index}", "status": draft.status})
        task.test_report = {"summary": {"test_point_count": len(drafts), "high_priority_count": sum(1 for x in drafts if x["priority"] == "high")}, "test_requirements": drafts, "regression_suggestions": sorted({x["file"].split("/")[0] for x in findings if x.get("file")}), "coverage_gaps": []}
        task.machine_coverage = 100
        task.ai_coverage = ai_coverage
        task.token_usage = token_usage
        ai_incomplete = task.mode != "quick" and bool(analyzable_diffs) and (ai_failed or ai_coverage == 0)
        final_status = "partial" if ai_incomplete else "completed"
        task.status, task.progress, task.current_step, task.completed_at = final_status, 100, "分析完成", timezone.now()
        task.save()
        return task
    except AnalysisCancelled:
        raise
    except Exception as exc:
        task.status, task.error_message, task.current_step = "failed", str(exc), "分析失败"
        task.save(update_fields=["status", "error_message", "current_step", "updated_at"])
        raise
