"""代码审查 MCP 的受限只读工具。

此模块不提供任意 shell；所有输入均来自已授权的 AnalysisTask，供审查编排调用。
"""
import json
import re
import subprocess
from pathlib import Path


class CodeReviewMCP:
    MAX_FILE_CHARS = 4000
    MAX_RESULTS = 12

    def __init__(self, task, client=None):
        self.task = task
        self.client = client

    def read_file(self, path, ref=None):
        """读取目标版本文件，最大 4k 字符。"""
        ref = ref or self.task.head_sha
        if self.task.repository.source_type == "local_git":
            from .services import LocalGitClient
            git = LocalGitClient(self.task.repository.local_path)
            return git._git("show", f"{ref}:{path}")[:self.MAX_FILE_CHARS]
        if not self.client:
            return ""
        return self.client.raw_file(self.task.repository.gitlab_project_id, path, ref)[:self.MAX_FILE_CHARS]

    def search_code(self, query, ref=None):
        """在本地 Git 目标提交中检索字面量；最多返回 12 个命中。"""
        if self.task.repository.source_type != "local_git" or not query or len(query) > 120:
            return []
        from .services import LocalGitClient
        git = LocalGitClient(self.task.repository.local_path)
        output = git._git("grep", "-n", "-I", "-e", query, ref or self.task.head_sha)
        return output.splitlines()[:self.MAX_RESULTS]

    def git_history(self, path):
        if self.task.repository.source_type != "local_git":
            return []
        from .services import LocalGitClient
        git = LocalGitClient(self.task.repository.local_path)
        return git._git("log", "--oneline", "-5", self.task.head_sha, "--", path).splitlines()

    def collect_context(self, diffs):
        """根据变更文件名及声明提取少量相关上下文，避免将全仓库塞进模型。"""
        items = []
        for diff in diffs[:8]:
            path, text = diff.get("path", ""), diff.get("diff", "")
            if not path:
                continue
            try:
                source = self.read_file(path)
            except Exception:
                continue
            declaration = re.findall(r"(?:class|interface|def|function|const)\s+([\w$-]+)", source)
            items.append({"path": path, "declarations": declaration[:8], "source": source})
        return items

    def run_static_checks(self, diffs):
        """运行仓库已安装的只读前端检查器；未安装时明确跳过而不伪造结果。"""
        if self.task.repository.source_type != "local_git":
            return [], ["GitLab 仓库尚未缓存到本地，未执行静态检查"]
        from .services import LocalGitClient
        root = LocalGitClient(self.task.repository.local_path).path
        vue_files = [item.get("path") for item in diffs if item.get("path", "").endswith(".vue")]
        findings, notes = [], []
        eslint = root / "node_modules" / ".bin" / "eslint"
        if vue_files and eslint.exists():
            try:
                result = subprocess.run([str(eslint), "--format", "json", *vue_files], cwd=root, text=True, capture_output=True, timeout=60)
                for report in json.loads(result.stdout or "[]"):
                    path = str(Path(report.get("filePath", "")).resolve().relative_to(root))
                    for message in report.get("messages", [])[:20]:
                        if message.get("severity") != 2:
                            continue
                        findings.append({"key": f"eslint:{path}:{message.get('line', 0)}:{message.get('ruleId', '')}", "change": f"ESLint：{message.get('message', '静态检查失败')}", "file": path, "severity": "high" if "parsing" in str(message.get("message", "")).lower() else "medium", "source": "static_scan", "confidence": 1.0, "verified": True, "line_start": message.get("line"), "evidence": message.get("source", "") or message.get("message", ""), "impact": f"Vue 模板或前端代码无法通过静态检查（{message.get('ruleId') or 'parser'}）"})
                notes.append("已执行 ESLint（仅变更 Vue 文件）")
            except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
                notes.append(f"ESLint 未完成：{exc}")
        elif vue_files:
            notes.append("仓库未安装 ESLint，跳过 Vue 静态检查")

        vue_tsc = root / "node_modules" / ".bin" / "vue-tsc"
        if vue_files and vue_tsc.exists():
            try:
                result = subprocess.run([str(vue_tsc), "--noEmit", "--pretty", "false"], cwd=root, text=True, capture_output=True, timeout=90)
                errors = [line for line in (result.stdout + "\n" + result.stderr).splitlines() if "error TS" in line]
                for line in errors[:20]:
                    match = re.match(r"(.+?)\((\d+),(\d+)\): error TS\d+: (.*)", line)
                    if not match:
                        continue
                    raw_path, row, _, message = match.groups()
                    candidate = Path(raw_path)
                    try:
                        path = str(candidate.resolve().relative_to(root)) if candidate.is_absolute() else str(candidate)
                    except ValueError:
                        path = str(candidate)
                    findings.append({"key": f"vue-tsc:{path}:{row}", "change": f"vue-tsc：{message}", "file": path, "severity": "high" if "template" in message.lower() else "medium", "source": "static_scan", "confidence": 1.0, "verified": True, "line_start": int(row), "evidence": line, "impact": "Vue 模板或 TypeScript 编译检查失败，相关页面可能无法构建或运行"})
                notes.append(f"已执行 vue-tsc，发现 {len(errors)} 条类型/模板错误")
            except subprocess.TimeoutExpired:
                notes.append("vue-tsc 超时，已跳过")
        return findings, notes
