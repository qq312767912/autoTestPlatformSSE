import fnmatch
import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import requests
from django.utils import timezone
from django.db import close_old_connections

from .models import AnalysisTask, AnalysisTaskExecutionLog, TestRequirementDraft, UserGitLabCredential


DEFAULT_ANNOTATIONS = {
    "Excel", "ExcelProperty", "JsonProperty", "JSONField", "NotNull", "NotBlank",
    "Size", "Transactional", "PreAuthorize", "RequestMapping", "GetMapping", "PostMapping",
    "PutMapping", "DeleteMapping", "PatchMapping", "Secured", "RolesAllowed", "Validated",
    "RequestBody", "PathVariable", "RequestParam", "ResponseBody", "Column", "Id",
}
API_ANNOTATIONS = {"RequestMapping", "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "PreAuthorize", "Secured", "RolesAllowed"}
PYTHON_DECORATORS = {"login_required", "permission_required", "require_http_methods", "api_view", "transaction.atomic"}
CRITICAL_MODIFIERS = {"public", "protected", "private", "static", "final", "synchronized", "volatile", "abstract"}
SENSITIVE_CONFIG_KEYS = {"enabled", "enable", "auth", "authentication", "authorization", "permission", "security", "ssl", "verify", "timeout", "retry", "url", "endpoint"}

# OpenCodeReview 参数统一维护。内网自建模型不限制 token；OCR 需要保留原生上下文，
# 否则多轮工具调用可能在上下文压缩阶段提前结束。
OCR_CONCURRENCY = 6
OCR_RESUME_CONCURRENCY = 4
OCR_GRACEFUL_STOP_SECONDS = 20
OCR_WORKSPACE_ROOT = Path(os.environ.get("OCR_WORKSPACE_ROOT", "/app/data/code-analysis-repositories"))
LOW_VALUE_FILE_PATTERNS = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "composer.lock", "Cargo.lock", "go.sum",
    "*.min.js", "*.min.css", "*.map", "*.snap", "*.generated.*", "*.designer.*",
    "dist/**", "build/**", "coverage/**", "vendor/**", "node_modules/**",
    "**/dist/**", "**/build/**", "**/coverage/**", "**/vendor/**", "**/node_modules/**",
)


def _ocr_timeout_budget(changed_lines):
    """按月度迭代规模分配 OCR 首轮和续审预算（分钟）。"""
    if changed_lines <= 1000:
        return 20, 10
    if changed_lines <= 3000:
        return 40, 20
    if changed_lines <= 8000:
        return 60, 30
    return 80, 40


def _git_changed_lines(root, base_ref, head_ref):
    """从 Git numstat 估算实际变更行；二进制文件不计入行数。"""
    result = subprocess.run(
        ["git", "-C", str(root), "--no-pager", "diff", "--numstat", base_ref, head_ref],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    if result.returncode != 0:
        return 0
    total = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    return total


def _is_low_value_file(path):
    """识别锁文件、构建产物等不适合消耗 Agent 审查时间的文件。"""
    normalized = (path or "").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern) for pattern in LOW_VALUE_FILE_PATTERNS)


class AnalysisCancelled(Exception):
    pass


def _ensure_not_cancelled(task):
    task.refresh_from_db(fields=["status"])
    if task.status == "cancelled":
        raise AnalysisCancelled("用户已取消分析")


class GitLabClient:
    def __init__(self, connection, token):
        self.base_url = connection.base_url.rstrip("/")
        # 公司内网 GitLab 可能使用自签名证书或以 IP 访问，所有 API 请求
        # 永久跳过 TLS 证书校验，不再受历史连接配置影响。
        self.verify = False
        self.headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}

    def get(self, path, params=None):
        response = requests.get(f"{self.base_url}/api/v4{path}", headers=self.headers, params=params, timeout=45, verify=self.verify)
        if not response.ok:
            try:
                payload = response.json()
                detail = payload.get("message") or payload.get("error") or payload
            except (ValueError, AttributeError):
                detail = (response.text or response.reason or "").strip()
            if response.status_code in {401, 403}:
                hint = "Token 无效、已过期或缺少 read_api/read_repository 权限"
            elif response.status_code == 404:
                hint = "GitLab 项目 ID/路径不正确，或当前 Token 无权访问该项目"
            else:
                hint = "GitLab API 请求失败"
            raise RuntimeError(f"{hint}（HTTP {response.status_code}：{detail}）")
        return response.json()

    def project(self, project_id):
        return self.get(f"/projects/{quote(str(project_id), safe='')}")

    def merge_requests(self, project_id):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/merge_requests", {"state": "opened", "per_page": 100})

    def commits(self, project_id, ref_name=None, limit=40):
        params = {"per_page": min(max(int(limit), 1), 40)}
        if ref_name:
            params["ref_name"] = ref_name
        return self.get(f"/projects/{quote(str(project_id), safe='')}/repository/commits", params)

    def commit(self, project_id, ref):
        return self.get(
            f"/projects/{quote(str(project_id), safe='')}/repository/commits/{quote(str(ref), safe='')}"
        )

    def merge_request_changes(self, project_id, iid):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/merge_requests/{iid}/changes")

    def compare(self, project_id, base_sha, head_sha):
        return self.get(f"/projects/{quote(str(project_id), safe='')}/repository/compare", {"from": base_sha, "to": head_sha, "straight": True})

    def raw_file(self, project_id, path, ref):
        encoded = quote(path, safe="")
        response = requests.get(f"{self.base_url}/api/v4/projects/{quote(str(project_id), safe='')}/repository/files/{encoded}/raw", headers=self.headers, params={"ref": ref}, timeout=20, verify=self.verify)
        response.raise_for_status()
        return response.text


class LocalGitClient:
    """本地开发验证专用的只读 Git 读取器。仓库只能位于 /workspace 挂载目录。"""
    ROOT = Path("/workspace").resolve()

    def __init__(self, relative_path):
        self.path = (self.ROOT / (relative_path or ".")).resolve()
        if self.path != self.ROOT and self.ROOT not in self.path.parents:
            raise ValueError("本地仓库路径不在允许范围内")
        if not (self.path / ".git").exists():
            raise ValueError(f"本地 Git 仓库不存在：{relative_path}")

    def _git(self, *args):
        # 历史仓库中可能存在 GBK 等非 UTF-8 文本；审查任务不能因单个文件编码失败。
        result = subprocess.run(
            ["git", "-C", str(self.path), "--no-pager", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "本地 Git 命令执行失败")
        return result.stdout

    def resolve(self, ref):
        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def commits(self, limit=40):
        output = self._git(
            "log", f"--max-count={min(max(int(limit), 1), 40)}",
            "--date=iso-strict", "--format=%H%x1f%h%x1f%s%x1f%an%x1f%aI",
        )
        commits = []
        for line in output.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) == 5:
                commits.append({
                    "id": parts[0], "short_id": parts[1], "title": parts[2],
                    "author_name": parts[3], "authored_date": parts[4],
                })
        return commits

    def compare(self, base_ref, head_ref):
        base_sha, head_sha = self.resolve(base_ref), self.resolve(head_ref)
        name_status = self._git("diff", "--name-status", "--find-renames", base_sha, head_sha)
        diffs = []
        for row in name_status.splitlines():
            parts = row.split("\t")
            if len(parts) < 2:
                continue
            status, old_path, new_path = parts[0], parts[-2] if len(parts) > 2 else parts[1], parts[-1]
            target = new_path if not status.startswith("D") else old_path
            diff = self._git("diff", "--no-ext-diff", "--find-renames", base_sha, head_sha, "--", target)
            diffs.append({"old_path": old_path, "new_path": new_path, "new_file": status.startswith("A"), "deleted_file": status.startswith("D"), "diff": diff})
        return {"base_sha": base_sha, "head_sha": head_sha, "diffs": diffs}


def _gitlab_clone_url(repository):
    """使用已配置的 GitLab 地址和项目路径构造不含凭证的 HTTPS clone URL。"""
    project_path = quote((repository.path_with_namespace or "").strip("/"), safe="/")
    if not project_path:
        raise ValueError("GitLab 仓库缺少项目路径")
    suffix = "" if project_path.endswith(".git") else ".git"
    return f"{repository.connection.base_url.rstrip('/')}/{project_path}{suffix}"


def _run_git(command, *, cwd, env, timeout=180):
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip()[-1000:] or "GitLab 临时仓库同步失败")
    return result.stdout


def remove_ocr_repository(task_id):
    """删除任务独占的 OCR 工作目录；只能由删除分析记录的流程调用。"""
    root = OCR_WORKSPACE_ROOT.resolve()
    target = (root / str(task_id)).resolve()
    if target.parent != root:
        raise ValueError("OCR 浅仓库路径越界")
    if target.exists():
        shutil.rmtree(target)


def remove_ocr_repositories_for_repository(repository_id):
    """清理已无审查记录仓库的残留 OCR 目录。

    仅删除内部标记明确匹配的任务目录，不会触碰 /workspace 下的本地源仓库。
    """
    root = OCR_WORKSPACE_ROOT.resolve()
    if not root.exists():
        return 0
    removed = 0
    for target in root.iterdir():
        if not target.is_dir() or target.parent.resolve() != root:
            continue
        marker = target / ".repository-id"
        try:
            matched = marker.read_text(encoding="utf-8").strip() == str(repository_id)
        except (FileNotFoundError, OSError):
            matched = False
        if matched:
            shutil.rmtree(target)
            removed += 1
    return removed


def _ocr_result_path(task):
    """返回独立的可写结果路径，绝不向只读的被审查仓库写文件。"""
    root = OCR_WORKSPACE_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    task_root = (root / str(task.pk)).resolve()
    if task_root.parent != root:
        raise ValueError("OCR 结果路径越界")
    task_root.mkdir(mode=0o700, exist_ok=True)
    return task_root / ".ocr-review-result.json"


@contextmanager
def _managed_gitlab_repository(task):
    """使用执行人的 Token创建或更新任务独占浅仓库，任务删除前持续保留。"""
    credential = UserGitLabCredential.objects.get(
        project=task.project,
        connection=task.repository.connection,
        user=task.executor or task.creator,
    )
    token = credential.get_token()
    OCR_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = OCR_WORKSPACE_ROOT / str(task.pk)
    root.mkdir(mode=0o700, exist_ok=True)
    (root / ".repository-id").write_text(str(task.repository_id), encoding="utf-8")
    askpass = root / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\ncase \"$1\" in *Username*) printf '%s\\n' oauth2 ;; *) printf '%s\\n' \"$OCR_GITLAB_TOKEN\" ;; esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = os.environ.copy()
    env.update({
        "GIT_ASKPASS": str(askpass),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSL_NO_VERIFY": "true",
        "OCR_GITLAB_TOKEN": token,
    })
    if not (root / ".git").exists():
        _run_git(["git", "init", "--quiet"], cwd=root, env=env)
        _run_git(["git", "remote", "add", "origin", _gitlab_clone_url(task.repository)], cwd=root, env=env)
    else:
        _run_git(["git", "remote", "set-url", "origin", _gitlab_clone_url(task.repository)], cwd=root, env=env)
    _run_git(["git", "fetch", "--quiet", "--force", "--no-tags", "--depth=50", "origin", f"{task.base_sha}:refs/ocr/base"], cwd=root, env=env)
    _ensure_not_cancelled(task)
    _run_git(["git", "fetch", "--quiet", "--force", "--no-tags", "--depth=50", "origin", f"{task.head_sha}:refs/ocr/head"], cwd=root, env=env)
    for deepen_by in (100, 250, 500):
        merge_base = subprocess.run(
            ["git", "merge-base", "refs/ocr/base", "refs/ocr/head"],
            cwd=root, env=env, capture_output=True, text=True, timeout=30,
        )
        if merge_base.returncode == 0:
            break
        _ensure_not_cancelled(task)
        _run_git([
            "git", "fetch", "--quiet", "--force", "--no-tags", f"--deepen={deepen_by}",
            "origin", task.base_sha, task.head_sha,
        ], cwd=root, env=env)
    merge_base = subprocess.run(
        ["git", "merge-base", "refs/ocr/base", "refs/ocr/head"],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    if merge_base.returncode != 0:
        raise ValueError("浅仓库在最大历史深度内找不到两个 Commit 的共同祖先")
    _run_git(["git", "checkout", "--quiet", "--force", "--detach", "refs/ocr/head"], cwd=root, env=env)
    yield root, "refs/ocr/base", "refs/ocr/head"


@contextmanager
def _ocr_repository(task):
    if task.repository.source_type == "local_git":
        yield LocalGitClient(task.repository.local_path).path, task.base_sha, task.head_sha
        return
    with _managed_gitlab_repository(task) as prepared:
        yield prepared


def _deleted_line_numbers(diff_text):
    """返回被删除文本在旧版本源码中的行号，供报告跳转和人工复核使用。"""
    result, old_line = {}, None
    for raw in diff_text.splitlines():
        hunk = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if hunk:
            old_line = int(hunk.group(1))
            continue
        if old_line is None:
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            result.setdefault(raw[1:].strip(), []).append(old_line)
            old_line += 1
        elif raw.startswith(" "):
            old_line += 1
    return result


def _parse_diff(diff_text, file_path, annotations):
    findings = []
    line_numbers = _deleted_line_numbers(diff_text)
    deleted = [line[1:].strip() for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")]
    for line in deleted:
        line_start = (line_numbers.get(line) or [None]).pop(0)
        annotation = re.match(r"@([A-Za-z_$][\w$]*)", line)
        if annotation and annotation.group(1) in annotations:
            name = annotation.group(1)
            impact = "接口暴露范围、路由映射或访问控制可能发生变化" if name in API_ANNOTATIONS else "字段映射、接口契约、权限、事务或校验行为可能发生变化"
            findings.append({
                "key": f"annotation:{file_path}:{name}:{len(findings)}", "change": f"删除 @{name} 注解",
                "file": file_path, "severity": "high", "source": "machine_rule", "confidence": 1.0,
                "evidence": line, "line_start": line_start, "impact": impact,
            })
        python_decorator = re.match(r"@([\w.]+)", line)
        if python_decorator and python_decorator.group(1) in PYTHON_DECORATORS:
            findings.append({
                "key": f"pydecorator:{file_path}:{python_decorator.group(1)}:{len(findings)}", "change": f"删除 Python 装饰器 @{python_decorator.group(1)}",
                "file": file_path, "severity": "high", "source": "machine_rule", "confidence": 0.95,
                "evidence": line, "line_start": line_start, "impact": "认证、权限、请求方法限制或事务边界可能失效",
            })
        if file_path.endswith((".yml", ".yaml", ".properties", ".env")):
            config_key = re.match(r"([A-Za-z][\w.-]*)\s*(?::|=)", line)
            if config_key and any(token in config_key.group(1).lower() for token in SENSITIVE_CONFIG_KEYS):
                findings.append({
                    "key": f"config:{file_path}:{len(findings)}", "change": f"删除关键配置：{config_key.group(1)}",
                    "file": file_path, "severity": "high", "source": "machine_rule", "confidence": 0.9,
                    "evidence": line, "line_start": line_start, "impact": "认证、安全通信、服务连通性或超时重试策略可能失效",
                })
        if file_path.endswith((".vue", ".tsx", ".jsx")) and ("v-permission" in line or "hasPermission" in line or "v-if" in line and "permission" in line.lower()):
            findings.append({
                "key": f"frontend-permission:{file_path}:{len(findings)}", "change": "删除前端权限控制条件",
                "file": file_path, "severity": "high", "source": "machine_rule", "confidence": 0.9,
                "evidence": line, "line_start": line_start, "impact": "受限操作可能在前端暴露，需验证后端鉴权与菜单可见性",
            })
        modifier_tokens = set(re.findall(r"\b(public|protected|private|static|final|synchronized|volatile|abstract)\b", line))
        if modifier_tokens & CRITICAL_MODIFIERS and re.search(r"\b(class|interface|enum|\w+\s*\()", line):
            findings.append({
                "key": f"modifier:{file_path}:{len(findings)}", "change": f"删除包含修饰符的声明：{line[:120]}",
                "file": file_path, "severity": "medium", "source": "machine_rule", "confidence": 0.9,
                "evidence": line, "line_start": line_start, "impact": "可见性、共享状态、不可变性或并发语义可能变化",
            })
    return findings


def _diff_line_stats(diff_text):
    additions = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return additions, deletions


def _impact_scope_count(files):
    """以变更文件的前两级目录归并影响范围；根目录文件单独计入。"""
    scopes = set()
    for item in files:
        path = item.get("path", "")
        parts = [part for part in path.split("/") if part]
        if parts:
            scopes.add("/".join(parts[:2]) if len(parts) > 1 else parts[0])
    return len(scopes)


def _test_point_for(finding):
    evidence = finding.get("evidence", "")
    if "@Excel" in evidence or "@ExcelProperty" in evidence:
        return {"title": "验证Excel导入导出字段完整性", "objective": "确认注解变更未导致列缺失、表头或顺序异常", "expected_result": "导入导出字段、表头、顺序和数据与需求一致", "priority": "high", "test_type": "风险排查"}
    if finding["key"].startswith(("annotation:", "pydecorator:", "frontend-permission:")):
        return {"title": "验证认证与权限边界", "objective": "确认变更后未授权用户无法访问受限接口、菜单或操作", "expected_result": "鉴权、授权、接口路由和前端可见性均符合原有权限规则", "priority": "high", "test_type": "风险排查"}
    if finding["key"].startswith("config:"):
        return {"title": "验证关键配置与异常降级", "objective": "确认部署配置删除或调整后服务安全连接、超时与重试策略仍有效", "expected_result": "服务可用，安全配置与失败场景处理符合发布要求", "priority": "high", "test_type": "风险排查"}
    # OCR 风险结论往往是长篇自然语言，不能原样拼进测试点标题。
    # 将它转为测试人员可执行的验证目标，而不是把审查意见伪装成测试需求。
    text = " ".join(str(finding.get(key, "")) for key in ("change", "evidence", "impact")).lower()
    if any(token in text for token in ("bare exception", "catching.*exception", "swallow", "instance.save", "persist", "trace_data")):
        return {"title": "验证异常处理与结果持久化", "objective": "模拟保存或数据持久化失败，确认异常不会被误判为成功，且后续读取不会重复执行无效处理", "expected_result": "失败状态、错误反馈与实际持久化结果一致；失败数据不会被标记为成功", "priority": finding.get("severity", "medium"), "test_type": "风险排查"}
    if any(token in text for token in ("exception", "异常", "error", "错误", "失败")):
        return {"title": "验证异常分支与错误反馈", "objective": "覆盖本次变更涉及的失败输入、依赖异常和重试场景", "expected_result": "异常被正确处理并返回可识别的失败结果，不影响正常业务流程", "priority": finding.get("severity", "medium"), "test_type": "风险排查"}
    if any(token in text for token in ("api", "接口", "request", "response", "route")):
        return {"title": "验证接口兼容性与边界输入", "objective": "覆盖变更接口的正常调用、参数边界和异常返回", "expected_result": "接口契约、返回结果和异常码符合既有约定", "priority": finding.get("severity", "medium"), "test_type": "风险排查"}
    if any(token in text for token in ("vue", "ui", "页面", "组件", "template")):
        return {"title": "验证前端交互与页面回归", "objective": "覆盖变更页面的关键交互、状态切换和异常提示", "expected_result": "页面可正常加载，关键操作与提示符合需求", "priority": finding.get("severity", "medium"), "test_type": "风险排查"}
    risk_title = str(finding.get("change") or "代码审查风险")
    risk_impact = str(finding.get("impact") or "代码审查报告描述的行为与影响")
    return {"title": f"排查：{risk_title}"[:500], "objective": f"根据代码审查证据构造对应触发条件，确认是否出现：{risk_impact}"[:1500], "expected_result": f"实际行为可明确判定，且不会产生代码审查报告指出的影响：{risk_impact}"[:1500], "priority": finding.get("severity", "medium"), "test_type": "风险排查"}


def normalize_test_point_for_display(point, findings):
    """兼容历史 OCR 草稿：旧版本把审查原文当作测试标题，展示/下载时实时规范化。

    不改写用户已经采纳或转为正式用例的原始数据，只修复报告呈现层。
    """
    normalized = dict(point)
    source_key = str(normalized.get("source_finding_key", ""))
    legacy_ocr = source_key.startswith("ocr:") and "OCR 分类" in str(normalized.get("objective", ""))
    if not legacy_ocr:
        return normalized
    finding = next((item for item in findings if item.get("key") == source_key), None)
    if not finding:
        return normalized
    normalized.update(_test_point_for(finding))
    return normalized


def normalize_finding_for_display(finding):
    """兼容历史 OCR 英文评论的中文呈现；新任务由 AI 中文整理提供更完整表述。"""
    normalized = dict(finding)
    if normalized.get("source") != "ocr_ai" or normalized.get("recommendation"):
        return normalized
    text = " ".join(str(normalized.get(key, "")) for key in ("change", "impact", "evidence")).lower()
    if "bare" in text and "exception" in text:
        normalized.update({
            "change": "异常捕获范围过宽，可能掩盖数据保存失败",
            "impact": "保存失败可能仍返回成功状态，后续请求会重复处理数据，导致状态与实际结果不一致",
            "recommendation": "区分可预期的数据库异常与其他异常；保存失败时返回失败结果，并验证重复请求行为",
        })
    else:
        normalized.update({
            "change": "OCR 发现潜在代码风险（需人工确认）",
            "impact": "请结合下方代码证据和完整 Diff 确认实际影响范围",
            "recommendation": "覆盖正常流程、异常分支及变更文件相关调用链后再决定是否修复",
        })
    return normalized


def _deduplicate_findings(findings):
    """同一文件、同一证据只保留确定性规则，避免 AI 重复放大风险数。"""
    unique, seen = [], set()
    for finding in findings:
        evidence = (finding.get("evidence") or "").strip()
        identity = (finding.get("file", ""), evidence)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(finding)
    return unique


def _json_from_response(content):
    text = content if isinstance(content, str) else str(content)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM未返回JSON对象")
    return json.loads(text[start:end + 1])


def _sanitize_json_value(value):
    """递归转义 PostgreSQL JSONB 不接受的控制字符，同时保留可读含义。"""
    if isinstance(value, str):
        return "".join(
            character if ord(character) >= 32 or character in "\t\n\r"
            else f"\\u{ord(character):04x}"
            for character in value
        )
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _sanitize_json_value(key): _sanitize_json_value(item)
            for key, item in value.items()
        }
    return value


def _is_unverified_syntax_claim(risk):
    """局部 Diff 无法可靠判断模板/编译语法，避免 AI 将单个闭合标签误报为高风险。"""
    text = " ".join(str(risk.get(field, "")) for field in ("change", "impact")).lower()
    return any(word in text for word in ("闭合标签", "未闭合", "语法错误", "编译错误", "template compile", "syntax error"))


def _load_ocr_payload(stdout):
    """兼容 OCR 在 JSON 前输出提示信息的情况，只接收最外层 JSON 对象。"""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _run_ocr_process(command, *, cwd, env, timeout, task):
    """运行 OCR；超时时先请求其写出部分结果，再停止整个进程组。"""
    # OCR CLI 会再拉起原生 opencodereview 子进程。仅 terminate 父进程会让子进程
    # 保持 stdout/stderr 管道，进而导致 communicate 一直等待、任务永久停在 65%。
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            _ensure_not_cancelled(task)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(5, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), False
            except subprocess.TimeoutExpired:
                continue
    except AnalysisCancelled:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)
        raise
    except subprocess.TimeoutExpired:
        # SIGINT 给 OCR 一个短暂收尾窗口，使其有机会把已完成分组写入 --output。
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=OCR_GRACEFUL_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate(timeout=5)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), True


def _analysis_stage(task_id, name, function, *args):
    """独立线程使用独立数据库连接，并记录真实阶段耗时。"""
    close_old_connections()
    started = time.monotonic()
    try:
        return function(*args)
    finally:
        try:
            AnalysisTaskExecutionLog.objects.create(
                task_id=task_id, event="stage_finished", message=f"{name}结束",
                detail={"stage": name, "elapsed_seconds": round(time.monotonic() - started, 2)},
            )
        finally:
            close_old_connections()


def _ocr_diagnostics(payload):
    """把 OCR manifest/retry_report 转成可展示、可审计的分组完整度诊断。"""
    manifest = payload.get("manifest") or {}
    coverage = manifest.get("coverage") or {}
    selected_items = coverage.get("selected") or []
    completed_items = coverage.get("completed") or []
    reused_items = coverage.get("reused") or []
    failed_items = coverage.get("failed") or []
    retry_report = payload.get("retry_report") or {}
    failed_requests = [item for item in retry_report.get("requests") or [] if item.get("outcome") == "failed"]
    recovered_requests = [item for item in retry_report.get("requests") or [] if item.get("outcome") == "recovered"]
    request_by_path = {}
    for request in failed_requests:
        for path in str(request.get("file_path") or "").split(","):
            if path.strip():
                request_by_path[path.strip()] = request

    failure_details, failure_type_counts = [], {}
    for item in failed_items:
        request = request_by_path.get(str(item.get("path") or ""), {})
        attempts = request.get("attempts") or []
        errors = [attempt for attempt in attempts if attempt.get("outcome") != "success"]
        status_codes = [attempt.get("status_code") for attempt in attempts if attempt.get("status_code")]
        if any(attempt.get("error_class") == "network" for attempt in errors):
            failure_type = "network"
        elif 429 in status_codes:
            failure_type = "rate_limit"
        elif any("timeout" in str(attempt.get("error_class") or "").lower() for attempt in errors):
            failure_type = "timeout"
        elif attempts and all(attempt.get("outcome") == "success" and attempt.get("status_code") == 200 for attempt in attempts):
            failure_type = "agent_subtask"
        else:
            failure_type = str(item.get("classification") or "unknown")
        failure_type_counts[failure_type] = failure_type_counts.get(failure_type, 0) + 1
        failure_details.append({
            "path": item.get("path", ""), "item_id": item.get("item_id", ""),
            "type": failure_type, "classification": item.get("classification", ""),
            "reason": item.get("reason", ""), "request_no": request.get("request_no"),
            "status_codes": status_codes,
        })

    failed_paths = {item.get("path") for item in failed_items}
    completed_paths = {item.get("path") for item in completed_items} | {item.get("path") for item in reused_items}
    group_details = []
    for group in payload.get("groups") or []:
        files = group.get("files") or []
        failed_files = [path for path in files if path in failed_paths]
        covered_files = [path for path in files if path in completed_paths]
        group_details.append({
            "label": group.get("label") or "未命名分组", "files": files,
            "completed": len(covered_files), "failed": len(failed_files),
            "coverage": round(len(covered_files) / len(files) * 100, 1) if files else 0,
            "failed_files": failed_files,
        })
    summary = payload.get("summary") or {}
    covered_count = len(completed_items) + len(reused_items)
    return {
        "selected": len(selected_items), "completed": len(completed_items),
        "reused": len(reused_items), "failed": len(failed_items),
        "coverage": round(covered_count / len(selected_items) * 100, 1) if selected_items else 0,
        "failure_type_counts": failure_type_counts, "failure_details": failure_details,
        "groups": group_details,
        "retry": {
            "total_requests": retry_report.get("total_requests", 0),
            "retried_requests": retry_report.get("retried_requests", 0),
            "recovered_requests": len(recovered_requests),
            "failed_requests": retry_report.get("failed_requests", len(failed_requests)),
        },
        "tool_failures": (payload.get("tool_calls") or {}).get("failure_details") or [],
        "elapsed": summary.get("elapsed", ""), "total_tokens": summary.get("total_tokens", 0),
        "configured_concurrency": (manifest.get("execution") or {}).get("configured_concurrency"),
        "ocr_version": (manifest.get("execution") or {}).get("ocr_version", ""),
    }


def _ocr_needs_resume(payload):
    """仅在存在可恢复会话和失败项时续审，避免重新分析成功文件。"""
    coverage = ((payload.get("manifest") or {}).get("coverage") or {}) if payload else {}
    return bool(payload and payload.get("session_id") and coverage.get("failed"))


def _run_open_code_review(task):
    """调用 OCR CLI；GitLab 使用个人 Token维护任务独占浅仓库。"""
    if task.mode == "quick":
        return [], 0, "快速模式未启用 OCR", False, 0, {}
    from langgraph_integration.models import LLMConfig
    config = LLMConfig.objects.filter(is_active=True).first()
    if not config:
        return [], 0, "未启用 OCR：没有可用 LLM 配置", False, 0, {}
    try:
        with _ocr_repository(task) as (root, base_ref, head_ref):
            changed_lines = _git_changed_lines(root, base_ref, head_ref)
            primary_timeout, resume_timeout = _ocr_timeout_budget(changed_lines)
            total_timeout = primary_timeout + resume_timeout
            env = os.environ.copy()
            env.update({"OCR_LLM_URL": config.api_url, "OCR_LLM_TOKEN": config.api_key, "OCR_LLM_MODEL": config.name, "OCR_LLM_PROTOCOL": "openai"})
            # 不传 --max-tokens，使用 OCR/模型的原生上下文上限；人为设为 5k/8k 会使
            # 多轮工具调用过早触发上下文压缩并中止审查。
            # OCR 仍以完整 Git 仓库作为只读工作目录，以便读取源码与历史；
            # JSON 结果单独写入 /app/data 下的任务工作目录。
            output_path = _ocr_result_path(task)
            output_path.unlink(missing_ok=True)
            command = [
                "ocr", "review", "--from", base_ref, "--to", head_ref,
                "--format", "json", "--audience", "agent",
                "--concurrency", str(OCR_CONCURRENCY),
                "--exclude", ",".join(LOW_VALUE_FILE_PATTERNS),
                "--timeout", str(primary_timeout),
                "--output", str(output_path),
            ]
            result, timed_out = _run_ocr_process(
                command, cwd=root, env=env, task=task,
                timeout=primary_timeout * 60 + OCR_GRACEFUL_STOP_SECONDS,
            )
            output_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            payload = _load_ocr_payload(output_text or result.stdout)
            resume_detail = {"triggered": False, "concurrency": OCR_RESUME_CONCURRENCY}
            if _ocr_needs_resume(payload):
                _ensure_not_cancelled(task)
                parent_session_id = payload["session_id"]
                first_diagnostics = _ocr_diagnostics(payload)
                resume_detail.update({
                    "triggered": True, "parent_session_id": parent_session_id,
                    "before_failed": first_diagnostics.get("failed", 0),
                    "before_coverage": first_diagnostics.get("coverage", 0),
                })
                resume_command = [
                    "ocr", "review", "--from", base_ref, "--to", head_ref,
                    "--format", "json", "--audience", "agent",
                    "--concurrency", str(OCR_RESUME_CONCURRENCY),
                    "--exclude", ",".join(LOW_VALUE_FILE_PATTERNS),
                    "--timeout", str(resume_timeout),
                    "--resume", parent_session_id,
                    "--output", str(output_path),
                ]
                try:
                    output_path.unlink(missing_ok=True)
                    resume_result, resume_timed_out = _run_ocr_process(
                        resume_command, cwd=root, env=env, task=task,
                        timeout=resume_timeout * 60 + OCR_GRACEFUL_STOP_SECONDS,
                    )
                    resumed_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
                    resumed_payload = _load_ocr_payload(resumed_text or resume_result.stdout)
                    if resumed_payload:
                        payload = resumed_payload
                        result, timed_out = resume_result, resume_timed_out
                    resume_detail["timed_out"] = resume_timed_out
                except AnalysisCancelled:
                    raise
                except Exception as resume_exc:
                    # 续审失败时继续使用首轮结果，绝不清空已经完成的 OCR 评论。
                    resume_detail.update({"status": "failed", "error": str(resume_exc)})
            diagnostics = _ocr_diagnostics(payload)
            if resume_detail.get("triggered"):
                resume_detail.setdefault("status", "completed" if diagnostics.get("failed", 0) == 0 else "partial")
                resume_detail.update({
                    "after_failed": diagnostics.get("failed", 0),
                    "after_coverage": diagnostics.get("coverage", 0),
                    "recovered_files": max(0, resume_detail.get("before_failed", 0) - diagnostics.get("failed", 0)),
                })
            diagnostics["resume"] = resume_detail
            diagnostics["timeout_budget"] = {
                "changed_lines": changed_lines, "primary_minutes": primary_timeout,
                "resume_minutes": resume_timeout, "total_minutes": total_timeout,
            }
        # OCR 会把 complete / partial / failed 都序列化为 JSON。只要收到了
        # 结构化输出，就保留 OCR 的真实覆盖情况，不能回退为旧的全量 Diff 扫描。
        status = payload.get("status")
        accepted_statuses = {"success", "complete", "partial", "partial_success", "completed", "completed_with_errors", "completed_with_warnings", "skipped", "failed"}
        if not payload or status not in accepted_statuses:
            raise ValueError(payload.get("message") or result.stderr[-500:] or f"OCR 返回状态：{status or 'empty'}")
        manifest = payload.get("manifest") or {}
        coverage = manifest.get("coverage") or {}
        selected = len(coverage.get("selected") or [])
        completed = len(coverage.get("completed") or [])
        reused = len(coverage.get("reused") or [])
        failed = len(coverage.get("failed") or [])
        covered = completed + reused
        review_coverage = round(covered / selected * 100, 1) if selected else (100 if status in {"success", "complete", "completed", "skipped"} else 0)
        severity = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
        # 部分 OpenAI 兼容服务在“无评论”时返回 comments: null，而非 []。
        findings = [{"key": f"ocr:{x.get('path')}:{x.get('start_line')}:{index}", "change": x.get("content", "OCR 审查提示"), "file": x.get("path", ""), "severity": severity.get(x.get("severity"), "medium"), "source": "ocr_ai", "confidence": 0.85, "verified": True, "line_start": x.get("start_line"), "evidence": x.get("existing_code") or x.get("content", ""), "impact": f"OCR 分类：{x.get('category') or 'other'}；建议结合上下文确认并回归"} for index, x in enumerate(payload.get("comments") or [])]
        if timed_out:
            note = f"OpenCodeReview 已达到本次 {total_timeout} 分钟动态上限，保留已完成结果（选中 {selected}，完成 {completed}，复用 {reused}，失败 {failed}，覆盖 {review_coverage}%）"
        elif status == "failed":
            note = f"OpenCodeReview 未获得可用覆盖（{payload.get('message') or result.stderr[-500:] or '请检查模型服务或 OCR 输出'}）"
        elif review_coverage < 100:
            note = f"已使用 OpenCodeReview 进行分组 Agent 审查（状态 {status}，选中 {selected}，完成 {completed}，复用 {reused}，失败 {failed}，覆盖 {review_coverage}%）"
        else:
            note = f"已使用 OpenCodeReview 进行分组 Agent 审查（状态 {status}，选中 {selected}，完成 {completed}，复用 {reused}，覆盖 {review_coverage}%）"
        tokens = int((payload.get("summary") or {}).get("total_tokens") or 0)
        # failed 且没有任何完成分组时才执行平台 AI 降级；存在已完成分组时保留部分结果。
        usable_result = status != "failed" or covered > 0
        return findings, tokens, note, usable_result, review_coverage, diagnostics
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as exc:
        return [], 0, f"OCR 不可用，已降级：{exc}", False, 0, {"failure_type_counts": {"platform": 1}, "failure_details": [{"type": "platform", "reason": str(exc)}]}


def _normalize_ocr_findings(task, findings):
    """把 OCR 的英文评论整理为中文审查结论，不改变其原始代码证据。"""
    if not findings:
        return findings, 0, ""
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance
    try:
        config = LLMConfig.objects.get(is_active=True)
        source = [{key: item.get(key) for key in ("key", "file", "change", "impact", "evidence")} for item in findings]
        prompt = (
            "你是中文代码审查编辑。将以下 OCR 审查意见改写成简明、可复核的中文。"
            "不得夸大风险，不得杜撰；保留原代码证据。输出严格 JSON："
            "{\"items\":[{\"key\":\"\",\"title\":\"中文问题概述\",\"impact\":\"实际影响\",\"recommendation\":\"修复或验证建议\"}]}。\n"
            f"OCR意见：{json.dumps(source, ensure_ascii=False)}"
        )
        response = create_llm_instance(config, temperature=0.1).invoke(prompt)
        payload = _json_from_response(getattr(response, "content", response))
        mapped = {item.get("key"): item for item in payload.get("items", [])}
        for finding in findings:
            item = mapped.get(finding["key"])
            if item:
                finding["change"] = str(item.get("title") or "OCR 发现潜在代码风险")[:500]
                finding["impact"] = str(item.get("impact") or "建议根据代码证据确认影响范围")[:1000]
                finding["recommendation"] = str(item.get("recommendation") or "结合代码上下文验证异常与边界场景")[:1000]
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        return findings, int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0), "OCR 审查意见已转换为中文结论"
    except Exception as exc:
        return findings, 0, f"OCR 中文整理未完成：{exc}"


def _target_file_content(task, path, review_client=None):
    """读取目标提交文件的完整内容，用于在隔离目录校验建议补丁。"""
    if task.repository.source_type == "local_git":
        return LocalGitClient(task.repository.local_path)._git("show", f"{task.head_sha}:{path}")
    if not review_client:
        return ""
    return review_client.raw_file(task.repository.gitlab_project_id, path, task.head_sha)


def _validate_suggested_patch(task, patch_text, review_client=None):
    """在目标版本文件的临时副本上执行 git apply --check，绝不修改真实仓库。"""
    paths = []
    for raw_path in re.findall(r"^(?:---|\+\+\+)\s+([^\t\n]+)", patch_text or "", re.MULTILINE):
        if raw_path == "/dev/null":
            continue
        path = raw_path[2:] if raw_path.startswith(("a/", "b/")) else raw_path
        if path not in paths:
            paths.append(path)
    if not paths:
        return False, "补丁未包含可识别的文件路径"
    try:
        with tempfile.TemporaryDirectory(prefix="wharttest-fix-check-") as directory:
            root = Path(directory).resolve()
            for path in paths:
                target = (root / path).resolve()
                target.relative_to(root)
                try:
                    content = _target_file_content(task, path, review_client)
                except (ValueError, OSError, subprocess.CalledProcessError):
                    content = ""
                target.parent.mkdir(parents=True, exist_ok=True)
                if content:
                    target.write_text(content, encoding="utf-8")
            result = subprocess.run(
                ["git", "apply", "--check", "--recount", "-"], cwd=root,
                input=patch_text, text=True, capture_output=True, timeout=15,
            )
            return result.returncode == 0, (result.stderr or result.stdout).strip()[-500:]
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _generate_fix_patches(task, findings, analyzable_diffs, review_client=None):
    """批量生成纯审阅补丁，并将可应用性校验结果写回对应风险。"""
    if task.mode == "quick" or not findings:
        for finding in findings:
            finding.update({"suggested_patch": "", "patch_status": "reference", "patch_validation_message": "快速模式不生成 AI 修复建议"})
        return 0, "快速模式未生成建议修复"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance
    for finding in findings:
        finding.update({"suggested_patch": "", "patch_status": "reference", "patch_validation_message": "未生成有效补丁"})
    try:
        config = LLMConfig.objects.get(is_active=True)
        diff_by_path = {item.get("path", ""): item.get("diff", "") for item in analyzable_diffs}
        source = []
        for finding in findings[:40]:
            source.append({
                "key": finding.get("key"), "file": finding.get("file"),
                "problem": finding.get("change"), "impact": finding.get("impact"),
                "recommendation": finding.get("recommendation"), "evidence": finding.get("evidence"),
                "original_diff": diff_by_path.get(finding.get("file", ""), "")[:6000],
            })
        prompt = (
            "你是代码修复补丁生成器。针对每条风险，基于目标版本代码和原始 Diff 生成最小化 unified diff。"
            "补丁必须应用于目标版本（变更完成后的代码），文件头使用 --- a/路径 和 +++ b/路径；"
            "不得输出 Markdown 代码围栏，不得修改与风险无关的代码，不确定时 patch 返回空字符串。"
            "仅输出严格 JSON：{\"items\":[{\"key\":\"\",\"patch\":\"完整 unified diff 或空字符串\"}]}。\n"
            f"风险与变更：{json.dumps(source, ensure_ascii=False)}"
        )
        response = create_llm_instance(config, temperature=0).invoke(prompt)
        payload = _json_from_response(getattr(response, "content", response))
        mapped = {str(item.get("key")): str(item.get("patch") or "").strip() for item in payload.get("items", [])}
        for finding in findings:
            patch_text = mapped.get(str(finding.get("key")), "")
            if patch_text.startswith("```"):
                patch_text = re.sub(r"^```(?:diff)?\s*|\s*```$", "", patch_text, flags=re.DOTALL).strip()
            finding["suggested_patch"] = patch_text[:100_000]
            if patch_text:
                applicable, message = _validate_suggested_patch(task, patch_text, review_client)
                finding["patch_status"] = "applicable" if applicable else "reference"
                finding["patch_validation_message"] = message or ("已通过 git apply --check" if applicable else "补丁未通过校验")
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        tokens = int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)
        applicable_count = sum(1 for item in findings if item.get("patch_status") == "applicable")
        return tokens, f"已生成建议修复，{applicable_count} 项通过 git apply --check"
    except Exception as exc:
        return 0, f"建议修复生成未完成：{exc}"


def generate_suggested_patch(task, finding, review_client=None):
    """按需为单个风险生成建议补丁；该流程只读源码，不会应用补丁。"""
    path = finding.get("file", "")
    marker = f"diff -- {path}\n"
    raw_diff = task.raw_diff or ""
    start = raw_diff.find(marker)
    if start >= 0:
        start += len(marker)
        end = raw_diff.find("\ndiff -- ", start)
        file_diff = raw_diff[start:end if end >= 0 else None]
    else:
        file_diff = ""
    generated = dict(finding)
    tokens, note = _generate_fix_patches(task, [generated], [{"path": path, "diff": file_diff}], review_client)
    return generated, tokens, note


def _validated_iteration_response(config, prompt):
    """格式或内容缺失时重新生成；失败必须交由报告状态显式处理。"""
    from langgraph_integration.views import create_llm_instance
    last_error = None
    diagnostics = []
    for attempt in range(3):
        try:
            instruction = prompt
            if attempt:
                instruction = '只输出一个完整 JSON 对象，禁止 Markdown 和解释文字。\n' + instruction
            response = create_llm_instance(config, temperature=0.1).invoke(
                instruction, response_format={"type": "json_object"}
            )
            content = getattr(response, 'content', response)
            extra = getattr(response, 'additional_kwargs', {}) or {}
            metadata = getattr(response, 'response_metadata', {}) or {}
            candidates = [content, extra.get('reasoning_content', '')]
            payload = None
            parse_errors = []
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    payload = _json_from_response(candidate)
                    break
                except Exception as parse_error:
                    parse_errors.append(str(parse_error))
            diagnostics.append({
                "attempt": attempt + 1,
                "content_length": len(content) if isinstance(content, str) else 0,
                "reasoning_length": len(extra.get('reasoning_content', '')),
                "finish_reason": metadata.get("finish_reason"),
                "parse_errors": parse_errors,
            })
            if payload is None:
                raise ValueError('LLM未返回可解析JSON对象')
            summary = payload.get('iteration_summary')
            points = payload.get('test_requirements')
            if not isinstance(summary, dict) or not summary.get('title') or not summary.get('change_groups'):
                raise ValueError('迭代总结缺失或结构不完整')
            if not isinstance(points, list) or not points or any(not isinstance(p, dict) or not all(p.get(k) for k in ('title', 'objective', 'expected_result')) for p in points):
                raise ValueError('需求测试点缺失或结构不完整')
            return response, payload
        except AnalysisCancelled:
            raise
        except Exception as exc:
            last_error = exc
    raise ValueError(f'迭代分析经过3次尝试仍未通过校验：{last_error}；响应诊断={json.dumps(diagnostics, ensure_ascii=False)}')


def _run_iteration_test_design(task, analyzable_diffs, findings):
    """基于本次 Diff 生成迭代测试点；风险仅作为补充约束，而非逐条转换。"""
    if task.mode == "quick" or not analyzable_diffs:
        return {}, [], 0, "快速模式未生成 AI 迭代测试点"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance
    try:
        config = LLMConfig.objects.get(is_active=True)
        max_chars = 18000 if task.mode == "standard" else 50000
        diff_context = "\n\n".join(f"文件：{item['path']}\n{item['diff'][:7000]}" for item in analyzable_diffs)[:max_chars]
        risks = [{key: item.get(key) for key in ("file", "change", "severity", "impact")} for item in findings if item.get("severity") in {"high", "medium"}][:40]
        prompt = (
            "你是资深测试分析师。基于本次代码 Diff 梳理本迭代可执行的测试需求。"
            "先理解变更带来的功能/流程调整，再设计正常流程、关键输入、异常分支、兼容或回归测试；"
            "风险列表仅用于补充风险验证，不能逐条照抄为测试点。无业务证据时不要生成空泛测试点。"
            "全程中文。先输出对本次迭代的精确总结，再按模块/业务域归类所有业务变化并拆分测试需求；文件只能作为变更依据，不能一文件对应一个测试点。"
            "必须完整输出所有识别到的业务变化，不设分组数量上限；总结不超过 80 字，每个分组不超过 2 句。"
            "先合并同一业务目标下的零散代码修改，再按变更规模从大到小排列；变更规模综合影响流程、涉及模块、文件范围和测试工作量判断。"
            "迭代总结必须分点输出，summary_points 中每个业务变化组至少对应一个独立要点，不设要点数量上限；"
            "要点按展示顺序排列，change_groups.name 必须是对应要点的简短中文小标题；"
            "summary_points 只写具体变化内容，不要自行添加序号，由报告统一生成 1、2、3、4 等序号。"
            "输出严格 JSON：{\"iteration_summary\":{\"title\":\"一句话迭代主题\",\"description\":\"一句话总体影响\",\"summary_points\":[\"与change_groups逐项对应的业务变化要点\"],\"change_groups\":[{\"module\":\"模块或业务域\",\"name\":\"业务变化名称\",\"description\":\"变化与影响\",\"change_scale\":\"large|medium|small\",\"files\":[\"文件路径\"],\"test_focus\":\"测试关注点\"}]},\"test_requirements\":[{\"change_group\":\"对应业务变化名称\",\"title\":\"\",\"objective\":\"\",\"expected_result\":\"\",\"priority\":\"high|medium|low\",\"test_type\":\"迭代验证\"}]}。\n"
            f"风险摘要：{json.dumps(risks, ensure_ascii=False)}\n本次Diff：\n{diff_context}"
        )
        response, payload = _validated_iteration_response(config, prompt)
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        points = [item for item in payload.get("test_requirements", []) if item.get("title") and item.get("objective")]
        iteration_summary = payload.get("iteration_summary") or {}
        groups = iteration_summary.get("change_groups") or []
        summary_points = iteration_summary.get("summary_points") or []
        scale_order = {"large": 3, "medium": 2, "small": 1}
        ranked = sorted(
            enumerate(groups),
            key=lambda pair: (
                scale_order.get(pair[1].get("change_scale"), 0),
                len(pair[1].get("files") or []),
                sum(1 for point in points if point.get("change_group") == pair[1].get("name")),
            ),
            reverse=True,
        )
        if ranked:
            iteration_summary["change_groups"] = [group for _, group in ranked]
            if len(summary_points) == len(groups):
                iteration_summary["summary_points"] = [summary_points[index] for index, _ in ranked]
        if not iteration_summary.get("summary_points"):
            iteration_summary["summary_points"] = [
                f"{item.get('name')}：{item.get('description')}"
                for item in iteration_summary.get("change_groups", [])
                if item.get("name") and item.get("description")
            ] or ([iteration_summary["description"]] if iteration_summary.get("description") else [])
        return iteration_summary, points, int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0), "AI 已基于代码变更完成迭代总结并梳理测试点"
    except Exception as exc:
        return {}, [], 0, f"AI 迭代测试点生成未完成：{exc}"


def _risk_findings_for_tests(findings):
    """只有代码审查报告中的高、中风险需要形成专项风险测试点。"""
    return [item for item in findings if item.get("severity") in {"high", "medium"}]


def _run_risk_test_design(task, findings):
    """将代码审查报告的高、中风险逐条转为可执行的风险排查点。

    风险测试点的唯一事实来源是代码审查报告，避免再次从 Diff 泛化出
    “验证核心流程”这类没有落点的模板化描述。
    """
    source_findings = _risk_findings_for_tests(findings)
    if not source_findings:
        return {}, 0, "代码审查报告没有高风险或中风险，无需生成风险排查点"
    points = {
        item["key"]: item["_risk_test_point"]
        for item in source_findings if item.get("_risk_test_point")
    }
    source_findings = [item for item in source_findings if item["key"] not in points]
    if not source_findings:
        return points, 0, "风险排查点已随代码审查同步生成"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance
    try:
        config = LLMConfig.objects.get(is_active=True)
        source = [
            {key: item.get(key, "") for key in ("key", "change", "file", "severity", "impact", "evidence", "recommendation")}
            for item in source_findings
        ]
        prompt = (
            "你是资深测试工程师。请仅依据下面的代码审查报告，为每一条风险生成一条可执行的风险排查测试点。"
            "每个输出项必须严格对应一个 risk_key，不能合并、遗漏或新增风险；优先级由系统继承，禁止输出优先级。"
            "必须直接引用该风险的具体问题、影响、代码证据和建议来表达测试目标，不能使用“验证核心业务流程”、"
            "“覆盖正常流程”等空泛措辞。若风险标题为通用占位语，必须从代码证据和影响中提炼实际待验证行为。"
            "全程中文，标题简短明确；测试目标写明输入/触发条件与需观察的行为；预期结果写明可判定结果。"
            "输出严格 JSON：{\"risk_test_points\":[{\"risk_key\":\"\",\"title\":\"\",\"objective\":\"\",\"expected_result\":\"\"}]}。\n"
            f"代码审查报告：\n{json.dumps(source, ensure_ascii=False)}"
        )
        response = create_llm_instance(config, temperature=0.1).invoke(prompt)
        payload = _json_from_response(getattr(response, "content", response))
        known_keys = {item["key"] for item in source_findings}
        for item in payload.get("risk_test_points", []):
            risk_key = str(item.get("risk_key", ""))
            if risk_key not in known_keys or not item.get("title") or not item.get("objective"):
                continue
            points[risk_key] = {
                "title": str(item["title"])[:500],
                "objective": str(item["objective"])[:1500],
                "expected_result": str(item.get("expected_result") or "风险场景的实际行为与代码审查结论一致")[:1500],
                "test_type": "风险排查",
            }
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        tokens = int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)
        return points, tokens, "已根据代码审查风险逐条生成风险排查点"
    except Exception as exc:
        return points, 0, f"风险排查点生成未完成，已使用规则化兜底：{exc}"


def _run_ai_batches(task, analyzable_diffs, machine_findings, review_client=None):
    """只向模型发送受控的小批量Diff；任何失败均降级为机器结果。"""
    if task.mode == "quick" or not analyzable_diffs:
        return [], [], [], 0, 0, "快速模式不调用LLM"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance

    try:
        config = LLMConfig.objects.get(is_active=True)
    except (LLMConfig.DoesNotExist, LLMConfig.MultipleObjectsReturned):
        return [], [], [], 0, 0, "没有唯一启用的LLM配置，已仅生成机器分析结果"

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

    ai_findings, test_points, impact_modules, used = [], [], [], 0
    from requirements.models import RequirementDocument
    def context_from_documents(document_ids, legacy_id, label):
        ids = list(document_ids or []) or ([legacy_id] if legacy_id else [])
        documents = RequirementDocument.objects.filter(id__in=ids, project=task.project).values_list("title", "content")
        if not documents:
            return f"{label}：未提供"
        # 上下文有总量上限，避免可选文档挤占 Diff 审查空间。
        parts, remaining = [], 12000
        for title, content in documents:
            part = f"【{title}】\n{(content or '文档尚未完成解析')[:remaining]}"
            parts.append(part)
            remaining -= len(part)
            if remaining <= 0:
                break
        return f"{label}：\n" + "\n\n".join(parts)
    business_context = "\n".join([
        context_from_documents(task.requirement_document_ids, task.requirement_document_id, "需求文档"),
        context_from_documents(task.api_document_ids, task.api_document_id, "接口文档"),
    ])
    # Agentic 审查的第一步：只读 MCP 按需收集变更文件的完整目标版本上下文。
    # 获取失败不影响原 Diff 审查。
    try:
        from .review_mcp import CodeReviewMCP
        repository_context = CodeReviewMCP(task, review_client).collect_context(analyzable_diffs)
    except Exception:
        repository_context = []
    prompt_head = (
        "你是测试代码审查助手。机器规则结论不可删除。只分析给出的局部 Diff，不推测未提供源码。"
        "按三步完成：先说明变更文件/模块；再识别类型（接口、SQL、配置、数据模型、权限、前端UI、任务/脚本等）；"
        "最后只基于明确证据评估影响范围并生成回归点。不得把单独出现的闭合标签、截断代码或未提供的上下文判断为模板语法/编译错误；"
        "模板、XML、Vue 等语法问题只有在同一 Diff 中存在明确不匹配证据时才可报告。"
        "对每条高风险或中风险，必须同时给出基于该风险结论的中文测试标题、触发条件和可判定预期；低风险无需给风险测试内容。"
        "输出严格JSON：{\"impact_modules\":[{\"module\":\"\",\"change_type\":\"\",\"impact\":\"\",\"regression_scope\":\"\"}],\"risks\":[{\"change\":\"\",\"file\":\"\",\"severity\":\"high|medium|low\","
        "\"confidence\":0.0,\"evidence\":\"\",\"impact\":\"\",\"test_title\":\"\",\"test_objective\":\"\",\"test_expected_result\":\"\"}],\"test_requirements\":[{\"title\":\"\","
        "\"objective\":\"\",\"expected_result\":\"\",\"priority\":\"high|medium|low\",\"test_type\":\"\"}]}。"
    )
    def analyze_batch(index, batch):
        # 各线程使用独立模型客户端，避免共享 HTTP 会话和回调状态。
        llm = create_llm_instance(config, temperature=0.1)
        known = [x for x in machine_findings if x.get("file") in batch]
        response = llm.invoke(f"{prompt_head}\n业务上下文：\n{business_context}\n已读取的目标版本源码上下文（只读MCP）：\n{json.dumps(repository_context, ensure_ascii=False)[:18000]}\n机器已确认风险：{json.dumps(known, ensure_ascii=False)}\nDIFF:\n{batch}")
        payload = _json_from_response(getattr(response, "content", response))
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        tokens = int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)
        return index, payload, tokens

    successful_batches, batch_errors = 0, []
    worker_count = min(3, len(batches))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="diff-review") as executor:
        futures = [executor.submit(analyze_batch, index, batch) for index, batch in enumerate(batches)]
        for index, future in enumerate(futures):
            try:
                batch_index, payload, batch_tokens = future.result()
            except Exception as exc:
                batch_errors.append(f"批次{index + 1}失败：{exc}")
                continue
            successful_batches += 1
            used += batch_tokens
            impact_modules.extend(payload.get("impact_modules", []))
            for risk_index, risk in enumerate(payload.get("risks", [])):
                if _is_unverified_syntax_claim(risk):
                    continue
                test_title = str(risk.pop("test_title", "") or "").strip()
                test_objective = str(risk.pop("test_objective", "") or "").strip()
                test_expected = str(risk.pop("test_expected_result", "") or "").strip()
                if risk.get("severity") in {"high", "medium"} and test_title and test_objective:
                    risk["_risk_test_point"] = {
                        "title": test_title[:500], "objective": test_objective[:1500],
                        "expected_result": (test_expected or "风险场景的实际行为与代码审查结论一致")[:1500],
                        "test_type": "风险排查",
                    }
                risk.update({"key": f"ai:{batch_index}:{risk_index}", "source": "ai_analysis"})
                ai_findings.append(risk)
            test_points.extend(payload.get("test_requirements", []))
    selected_coverage = min(len(analyzable_diffs), max_files) / len(analyzable_diffs) * 100
    batch_coverage = successful_batches / len(batches) if batches else 0
    coverage = round(selected_coverage * batch_coverage, 1)
    if batch_errors:
        note = f"AI并行分析完成 {successful_batches}/{len(batches)} 个批次；" + "；".join(batch_errors)
    else:
        note = "AI已并行完成全部 Diff 批次分析" if coverage == 100 else f"AI仅分析前{max_files}个文件，其余保留机器分析"
    return ai_findings, test_points, impact_modules, used, coverage, note


def _run_context_test_enrichment(task, findings):
    """OCR 已完成源码审查后，以可选业务文档补充测试需求和回归范围。

    文档不会直接塞入 OCR 的多轮源码上下文，避免影响代码阅读；仅作为测试设计约束使用。
    """
    requirement_ids = list(task.requirement_document_ids or []) or ([task.requirement_document_id] if task.requirement_document_id else [])
    api_ids = list(task.api_document_ids or []) or ([task.api_document_id] if task.api_document_id else [])
    if not (requirement_ids or api_ids):
        return [], [], 0, "未关联需求或接口文档，未执行业务上下文补充"
    from langgraph_integration.models import LLMConfig
    from langgraph_integration.views import create_llm_instance
    from requirements.models import RequirementDocument
    try:
        config = LLMConfig.objects.get(is_active=True)
        document_ids = list(dict.fromkeys(requirement_ids + api_ids))
        documents = RequirementDocument.objects.filter(project=task.project, id__in=document_ids).values("title", "content")
        context = "\n\n".join(f"文档：{item['title']}\n{(item['content'] or '')[:8000]}" for item in documents)
        if not context:
            return [], [], 0, "关联文档尚未解析，未执行业务上下文补充"
        risk_context = [{key: value for key, value in finding.items() if key in {"change", "file", "severity", "impact", "evidence"}} for finding in findings[:80]]
        prompt = (
            "你是测试设计助手。源码审查已完成，不能重复或修改其中的风险结论。"
            "仅根据业务/接口文档和已确认风险，补充可执行的测试需求及影响模块；"
            "每一项必须体现文档或风险中的明确依据，不要臆造接口、字段或业务规则。"
            "输出严格 JSON：{\"impact_modules\":[{\"module\":\"\",\"change_type\":\"\",\"impact\":\"\",\"regression_scope\":\"\"}],"
            "\"test_requirements\":[{\"title\":\"\",\"objective\":\"\",\"expected_result\":\"\",\"priority\":\"high|medium|low\",\"test_type\":\"\"}]}。\n"
            f"业务文档：\n{context}\n\n已确认风险：\n{json.dumps(risk_context, ensure_ascii=False)}"
        )
        response = create_llm_instance(config, temperature=0.1).invoke(prompt)
        payload = _json_from_response(getattr(response, "content", response))
        usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("token_usage", {})
        tokens = int(usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or 0)
        return payload.get("test_requirements", []), payload.get("impact_modules", []), tokens, "已结合需求/接口文档补充测试需求与回归范围"
    except Exception as exc:
        return [], [], 0, f"业务文档补充未完成：{exc}"


def _cache_report_complete(task):
    """不复用 OCR 失败或缺少迭代分析的历史结果。"""
    change, report = task.change_report or {}, task.test_report or {}
    if not change or not report or not task.raw_diff:
        return False
    if task.mode == "quick":
        return True
    ocr = change.get("ocr_status") or {}
    summary = report.get("iteration_summary") or {}
    return (
        ocr.get("status") == "completed" and ocr.get("coverage") == 100
        and bool(summary.get("title")) and bool(summary.get("change_groups"))
        and any(
            point.get("test_type") == "迭代验证"
            and all(point.get(key) for key in ("title", "objective", "expected_result"))
            for point in report.get("test_requirements", [])
        )
    )


def _reuse_cached_result(task, original_base_sha, original_head_sha):
    """相同仓库、Commit、模式与上下文直接复用已完成报告。"""
    source = None
    if (
        _cache_report_complete(task)
        and original_base_sha == task.base_sha and original_head_sha == task.head_sha
    ):
        source = task
    if source is None:
        candidates = AnalysisTask.objects.filter(
            repository=task.repository, base_sha=task.base_sha, head_sha=task.head_sha,
            mode=task.mode, requirement_context=task.requirement_context, api_context=task.api_context,
            requirement_document_ids=task.requirement_document_ids,
            api_document_ids=task.api_document_ids,
            status="completed",
        ).exclude(pk=task.pk).order_by("-completed_at")
        source = next((candidate for candidate in candidates.iterator() if _cache_report_complete(candidate)), None)
    if source is None:
        return False

    change_report = json.loads(json.dumps(source.change_report or {}, ensure_ascii=False))
    test_report = json.loads(json.dumps(source.test_report or {}, ensure_ascii=False))
    cached_points = test_report.get("test_requirements") or []
    rebuilt_points = []
    for point in cached_points:
        existing = None
        if source.pk == task.pk and point.get("id"):
            existing = TestRequirementDraft.objects.filter(task=task, pk=point["id"]).first()
        if existing:
            rebuilt_points.append(dict(point))
            continue
        draft = TestRequirementDraft.objects.create(
            task=task,
            title=str(point.get("title") or "代码变更验证")[:500],
            objective=str(point.get("objective") or ""),
            expected_result=str(point.get("expected_result") or ""),
            priority=str(point.get("priority") or "medium")[:20],
            test_type=str(point.get("test_type") or "迭代验证")[:100],
            source_finding_key=str(point.get("source_finding_key") or "")[:255],
        )
        rebuilt = dict(point)
        rebuilt.update({"id": draft.id, "status": draft.status})
        rebuilt_points.append(rebuilt)
    test_report["test_requirements"] = rebuilt_points
    change_report["cache"] = {"reused": True, "source_task_id": str(source.pk)}
    task.change_report = change_report
    task.test_report = test_report
    task.raw_diff = source.raw_diff
    task.machine_coverage = source.machine_coverage
    task.ai_coverage = source.ai_coverage
    task.token_usage = 0
    task.status = "partial" if task.mode != "quick" and source.ai_coverage < 100 else "completed"
    task.progress = 100
    task.current_step = "已复用相同 Commit 的分析缓存"
    task.completed_at = timezone.now()
    task.save()
    AnalysisTaskExecutionLog.objects.create(
        task=task, event=task.status, message="已复用相同 Commit 的分析缓存",
        detail={"cache_hit": True, "source_task_id": str(source.pk)},
    )
    return True


def run_analysis(task: AnalysisTask, force_refresh=False):
    original_base_sha, original_head_sha = task.base_sha, task.head_sha
    _ensure_not_cancelled(task)
    AnalysisTaskExecutionLog.objects.create(task=task, event="started", message="后台任务开始执行")
    task.status, task.progress, task.current_step = "fetching", 10, "读取代码变更"
    task.save(update_fields=["status", "progress", "current_step", "updated_at"])
    try:
        if task.repository.source_type == "local_git":
            if task.source_type != "commits":
                raise ValueError("本地 Git 审查仅支持两个分支或 Commit 对比")
            payload = LocalGitClient(task.repository.local_path).compare(task.base_sha, task.head_sha)
            task.base_sha, task.head_sha = payload["base_sha"], payload["head_sha"]
            task.title = task.title or f"本地 Git：{task.repository.name}"
            diffs = payload["diffs"]
        else:
            credential = UserGitLabCredential.objects.get(project=task.project, connection=task.repository.connection, user=task.executor or task.creator)
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

        if not force_refresh and _reuse_cached_result(task, original_base_sha, original_head_sha):
            return task

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "machine_analyzing", 40, "检测关键代码变化"
        task.save(update_fields=["status", "progress", "current_step", "base_sha", "head_sha", "title", "updated_at"])
        patterns = task.repository.excluded_patterns or []
        annotations = DEFAULT_ANNOTATIONS | set(task.repository.critical_annotations or [])
        files, findings, raw_parts, analyzable_diffs = [], [], [], []
        total_additions, total_deletions = 0, 0
        for item in diffs:
            path = item.get("new_path") or item.get("old_path") or ""
            configured_excluded = any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
            low_value = _is_low_value_file(path)
            excluded = configured_excluded or low_value
            diff = item.get("diff", "")
            additions, deletions = _diff_line_stats(diff)
            total_additions += additions
            total_deletions += deletions
            files.append({"path": path, "old_path": item.get("old_path"), "new_file": item.get("new_file", False), "deleted_file": item.get("deleted_file", False), "excluded": excluded, "exclusion_reason": "低价值文件" if low_value else ("仓库排除规则" if configured_excluded else ""), "additions": additions, "deletions": deletions, "changed_lines": additions + deletions})
            if excluded and not item.get("deleted_file"):
                continue
            raw_parts.append(f"diff -- {path}\n{diff}")
            analyzable_diffs.append({"path": path, "diff": diff})
            findings.extend(_parse_diff(diff, path, annotations))
            if item.get("deleted_file"):
                findings.append({"key": f"file_deleted:{path}", "change": "删除文件", "file": path, "severity": "medium", "source": "machine_rule", "confidence": 1.0, "evidence": path, "impact": "依赖该文件的功能可能受到影响"})

        # 确定性工具优先：只有实际运行的静态检查结果才能标记为 static_scan 风险。
        try:
            from .review_mcp import CodeReviewMCP
            static_findings, static_notes = CodeReviewMCP(task, client if task.repository.source_type != "local_git" else None).run_static_checks(analyzable_diffs)
            findings.extend(static_findings)
        except Exception as exc:
            static_notes = [f"静态检查不可用：{exc}"]

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "ai_analyzing", 65, "分批分析语义风险"
        task.save(update_fields=["status", "progress", "current_step", "updated_at"])
        ai_failed = False
        # 迭代需求来自 Diff，不依赖 OCR 风险；两项可同时执行。
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iteration-design")
        ocr_note, ocr_completed, ocr_coverage, ocr_diagnostics = "OCR 尚未执行", False, 0, {}
        iteration_future = executor.submit(
            _analysis_stage, task.pk, "迭代需求分析", _run_iteration_test_design,
            task, analyzable_diffs, list(findings),
        )
        try:
            ocr_findings, ocr_tokens, ocr_note, ocr_completed, ocr_coverage, ocr_diagnostics = _analysis_stage(task.pk, "OCR审查", _run_open_code_review, task)
            if ocr_completed:
                ocr_findings, chinese_tokens, chinese_note = _normalize_ocr_findings(task, ocr_findings)
                findings.extend(ocr_findings)
                ai_findings, iteration_test_points, impact_modules, enrichment_tokens, enrichment_note = [], [], [], 0, ""
                fallback_tokens, fallback_note = 0, ""
                failed_paths = {item.get("path") for item in ocr_diagnostics.get("failure_details", []) if item.get("path")}
                fallback_diffs = [item for item in analyzable_diffs if item.get("path") in failed_paths]
                if fallback_diffs:
                    try:
                        fallback_findings, _unused_points, fallback_modules, fallback_tokens, fallback_coverage, fallback_note = _run_ai_batches(
                            task, fallback_diffs, findings,
                            client if task.repository.source_type != "local_git" else None,
                        )
                        findings.extend(fallback_findings)
                        impact_modules.extend(fallback_modules)
                        selected_count = int(ocr_diagnostics.get("selected") or len(analyzable_diffs) or 1)
                        ocr_covered = int(ocr_diagnostics.get("completed") or 0) + int(ocr_diagnostics.get("reused") or 0)
                        fallback_covered = len(fallback_diffs) * fallback_coverage / 100
                        effective_coverage = round(min(100, (ocr_covered + fallback_covered) / selected_count * 100), 1)
                        ocr_diagnostics["fallback"] = {
                            "triggered": True, "status": "completed" if fallback_coverage >= 100 else "partial",
                            "files": sorted(failed_paths), "file_count": len(fallback_diffs),
                            "coverage": fallback_coverage, "effective_coverage": effective_coverage,
                            "note": fallback_note,
                        }
                    except AnalysisCancelled:
                        raise
                    except Exception as fallback_exc:
                        # OCR 已完成的分组仍然有效；补审失败不能中断迭代总结和测试点生成。
                        fallback_coverage, effective_coverage = 0, ocr_coverage
                        fallback_note = f"OCR失败文件补审失败，已保留OCR完成结果：{fallback_exc}"
                        ocr_diagnostics["fallback"] = {
                            "triggered": True, "status": "failed", "files": sorted(failed_paths),
                            "file_count": len(fallback_diffs), "coverage": 0,
                            "effective_coverage": ocr_coverage, "note": fallback_note,
                        }
                else:
                    fallback_coverage, effective_coverage = 0, ocr_coverage
                    ocr_diagnostics["fallback"] = {"triggered": False, "file_count": 0, "coverage": 0, "effective_coverage": ocr_coverage}
                document_test_points, document_modules, enrichment_tokens, enrichment_note = _run_context_test_enrichment(task, findings)
                impact_modules.extend(document_modules)
                token_usage, ai_coverage = ocr_tokens + chinese_tokens + fallback_tokens + enrichment_tokens, effective_coverage
                ai_note = f"{ocr_note}；{chinese_note}；{fallback_note}；{enrichment_note}"
            else:
                ai_findings, _unused_points, impact_modules, token_usage, ai_coverage, ai_note = _run_ai_batches(task, analyzable_diffs, findings, client if task.repository.source_type != "local_git" else None)
                ai_note = f"{ocr_note}；{ai_note}"
                findings.extend(ai_findings)
                document_test_points, document_modules, enrichment_tokens, enrichment_note = _run_context_test_enrichment(task, findings)
                impact_modules.extend(document_modules)
                token_usage += enrichment_tokens
            AnalysisTaskExecutionLog.objects.create(
                task=task, event="stage_finished", message="OCR 完整度诊断",
                detail={"stage": "OCR完整度", **ocr_diagnostics},
            )
            iteration_summary, iteration_test_points, iteration_tokens, iteration_note = iteration_future.result()
            iteration_test_points.extend(document_test_points)
            token_usage += iteration_tokens
            ai_note = f"{ai_note}；{iteration_note}"
        except AnalysisCancelled:
            raise
        except Exception as exc:
            ai_findings, iteration_summary, iteration_test_points, impact_modules, token_usage, ai_coverage = [], {}, [], [], 0, 0
            ai_note, ai_failed = f"AI分析失败，已保留机器结果：{exc}", True
            # 审查失败时仍保留独立生成的迭代需求。
            iteration_summary, iteration_test_points, iteration_tokens, iteration_note = iteration_future.result()
            token_usage += iteration_tokens
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        _ensure_not_cancelled(task)
        task.status, task.progress, task.current_step = "generating_tests", 82, "生成测试分析报告"
        task.save(update_fields=["status", "progress", "current_step", "updated_at"])
        findings = _deduplicate_findings(findings)
        for finding in findings:
            finding["verified"] = finding.get("source") != "ai_analysis"
        # 建议修复不阻塞主报告。用户在报告中点击后，才对单个风险生成并校验补丁。
        for finding in findings:
            finding.pop("suggested_patch", None)
            finding.pop("patch_status", None)
            finding.pop("patch_validation_message", None)
        risk_test_points, risk_test_tokens, risk_test_note = _run_risk_test_design(task, findings)
        for finding in findings:
            finding.pop("_risk_test_point", None)
        token_usage += risk_test_tokens
        ai_note = f"{ai_note}；建议修复改为按需生成；{risk_test_note}"
        task.raw_diff = "\n\n".join(raw_parts)[:2_000_000]
        severity_counts = {}
        source_counts = {}
        for finding in findings:
            severity_counts[finding["severity"]] = severity_counts.get(finding["severity"], 0) + 1
            source_counts[finding["source"]] = source_counts.get(finding["source"], 0) + 1
        if task.mode == "quick":
            ocr_status = {"status": "skipped", "message": "快速模式未启用 OCR", "coverage": 0, "diagnostics": ocr_diagnostics}
        elif ocr_completed and ocr_coverage >= 100:
            ocr_status = {"status": "completed", "message": "OCR 审查已完成", "coverage": ocr_coverage, "diagnostics": ocr_diagnostics}
        elif ocr_completed:
            ocr_status = {"status": "partial", "message": ocr_note, "coverage": ocr_coverage, "diagnostics": ocr_diagnostics}
        else:
            ocr_status = {"status": "failed", "message": ocr_note, "coverage": 0, "diagnostics": ocr_diagnostics}
        task.change_report = _sanitize_json_value({
            "summary": {"changed_files": len(files), "impact_scope_count": _impact_scope_count(files), "additions": total_additions, "deletions": total_deletions, "changed_lines": total_additions + total_deletions, "risk_count": len(findings), "high_risk_count": sum(1 for x in findings if x["severity"] == "high"), "severity_counts": severity_counts, "source_counts": source_counts},
            "files": files, "findings": findings, "impact_modules": impact_modules,
            "impact_summary": sorted({x["impact"] for x in findings}),
            "analysis_note": "；".join([*static_notes, ai_note]),
            "ocr_status": ocr_status,
        })
        drafts = []
        # 代码审查报告中的高、中风险逐条生成风险测试点；低风险不生成专项测试点。
        # 优先级严格继承源风险等级，保留风险键以供报告追溯。
        for finding in _risk_findings_for_tests(findings):
            point = _sanitize_json_value(risk_test_points.get(finding["key"], _test_point_for(finding)))
            point["priority"] = finding.get("severity", "medium")
            point["test_type"] = "风险排查"
            draft = TestRequirementDraft.objects.create(task=task, source_finding_key=finding["key"], **point)
            drafts.append({"id": draft.id, **point, "change_group": "风险排查", "source_finding_key": finding["key"], "risk_reference": {"key": finding["key"], "title": finding.get("change", "代码审查风险"), "file": finding.get("file", ""), "severity": finding.get("severity", "medium")}, "status": draft.status})
        for index, point in enumerate(iteration_test_points):
            safe_point = _sanitize_json_value({
                "title": str(point.get("title", "代码变更回归验证"))[:500],
                "objective": point.get("objective", "验证代码变更影响"),
                "expected_result": point.get("expected_result", "相关功能符合需求"),
                "priority": str(point.get("priority", "medium"))[:20],
                # 不论测试点来自 Diff 还是需求/接口文档，均属于本次迭代验证；
                # 风险排查仅由上方已关联代码审查风险的流程生成。
                "test_type": "迭代验证",
            })
            draft = TestRequirementDraft.objects.create(task=task, source_finding_key=f"iteration-test:{index}", **safe_point)
            drafts.append({"id": draft.id, **safe_point, "change_group": str(point.get("change_group") or "本次迭代")[ :200], "source_finding_key": f"iteration-test:{index}", "status": draft.status})
        coverage_gaps = [f"已排除：{item['path']}" for item in files if item["excluded"]]
        iteration_incomplete = task.mode != 'quick' and bool(analyzable_diffs) and (not iteration_summary or not iteration_test_points)
        if iteration_incomplete:
            coverage_gaps.append('迭代分析未完成：迭代总结或需求测试点缺失，需要重新生成')
        if task.mode != "quick" and ai_coverage < 100:
            coverage_gaps.append(f"AI仅覆盖 {ai_coverage}% 的可分析文件，其余仅执行机器规则")
        test_type_counts = {}
        for draft in drafts:
            test_type_counts[draft["test_type"]] = test_type_counts.get(draft["test_type"], 0) + 1
        task.test_report = _sanitize_json_value({"summary": {"test_point_count": len(drafts), "high_priority_count": sum(1 for x in drafts if x["priority"] == "high"), "test_type_counts": test_type_counts}, "iteration_summary": iteration_summary, "test_requirements": drafts, "regression_suggestions": sorted({x["file"].split("/")[0] for x in findings if x.get("file")}), "coverage_gaps": coverage_gaps})
        task.machine_coverage = 100
        task.ai_coverage = ai_coverage
        task.token_usage = token_usage
        ai_incomplete = task.mode != "quick" and bool(analyzable_diffs) and (ai_failed or ai_coverage < 100 or iteration_incomplete)
        final_status = "partial" if ai_incomplete else "completed"
        task.status, task.progress, task.current_step, task.completed_at = final_status, 100, "分析完成", timezone.now()
        task.save()
        AnalysisTaskExecutionLog.objects.create(
            task=task, event=final_status, message="分析完成" if final_status == "completed" else "分析部分完成，请查看覆盖缺口",
            detail={"machine_coverage": task.machine_coverage, "ai_coverage": task.ai_coverage, "risk_count": len(findings)},
        )
        return task
    except AnalysisCancelled:
        AnalysisTaskExecutionLog.objects.create(task=task, event="cancelled", message="后台任务响应取消请求")
        raise
    except Exception as exc:
        task.status, task.error_message, task.current_step = "failed", str(exc), "分析失败"
        task.save(update_fields=["status", "error_message", "current_step", "updated_at"])
        AnalysisTaskExecutionLog.objects.create(task=task, event="failed", message="后台执行失败", detail={"error": str(exc)[:1000]})
        raise
