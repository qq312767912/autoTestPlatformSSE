"""代码审查 MCP 的受限只读工具。

此模块不提供任意 shell；所有输入均来自已授权的 AnalysisTask，供审查编排调用。
"""
import json
import re
import shutil
import subprocess
import os
from contextlib import contextmanager
from pathlib import Path


class CodeReviewMCP:
    MAX_FILE_CHARS = 20000
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

    @staticmethod
    def _language_for_path(path):
        return {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "tsx", ".java": "java",
            ".go": "go", ".rs": "rust", ".c": "c", ".h": "c",
            ".cpp": "cpp", ".cc": "cpp", ".cs": "c_sharp",
        }.get(Path(path).suffix.lower())

    def _ast_chunks(self, path, source):
        """优先使用 Tree-sitter 按声明切块；运行环境缺少语法包时显式降级。"""
        language = self._language_for_path(path)
        if not language or not source:
            return [], "unsupported"
        try:
            from tree_sitter import Language, Parser
            module_name = {
                "python": "tree_sitter_python", "javascript": "tree_sitter_javascript",
                "typescript": "tree_sitter_typescript", "tsx": "tree_sitter_typescript",
                "java": "tree_sitter_java",
            }.get(language)
            if not module_name:
                return [], "unsupported"
            module = __import__(module_name)
            language_factory = (
                module.language_tsx if language == "tsx"
                else module.language_typescript if language == "typescript"
                else module.language
            )
            tree = Parser(Language(language_factory())).parse(source.encode("utf-8", errors="replace"))
        except (ImportError, AttributeError, LookupError, RuntimeError, TypeError, ValueError):
            return [], "fallback"
        interesting = {
            "class_definition", "function_definition", "method_definition",
            "function_declaration", "method_declaration", "class_declaration",
            "interface_declaration", "function_item", "impl_item",
        }
        chunks = []
        stack = [tree.root_node]
        while stack and len(chunks) < 30:
            node = stack.pop()
            if node.type in interesting:
                text = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                chunks.append({
                    "kind": node.type, "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1, "content": text[:6000],
                })
                continue
            stack.extend(reversed(node.named_children))
        return chunks, "tree-sitter"

    def collect_context(self, diffs):
        """AST 精确切块优先，并用声明/调用标识符检索相关源码作为 RAG 上下文。"""
        items = []
        engines = set()
        for diff in diffs:
            path, text = diff.get("path", ""), diff.get("diff", "")
            if not path:
                continue
            try:
                source = self.read_file(path)
            except Exception:
                continue
            chunks, engine = self._ast_chunks(path, source)
            engines.add(engine)
            declarations = re.findall(r"(?:class|interface|def|function|const)\s+([\w$-]+)", source)
            related = []
            for symbol in declarations[:6]:
                try:
                    related.extend(self.search_code(symbol))
                except Exception:
                    pass
            items.append({
                "path": path, "engine": engine, "declarations": declarations[:12],
                "ast_chunks": chunks, "related_code": list(dict.fromkeys(related))[:20],
                "source": source[:6000] if not chunks else "",
            })
        return {"parser": "tree-sitter" if "tree-sitter" in engines else "fallback", "items": items}

    @contextmanager
    def _repository_root(self):
        if self.task.repository.source_type == "local_git":
            from .services import LocalGitClient
            yield LocalGitClient(self.task.repository.local_path).path
            return
        from .services import _managed_gitlab_repository
        with _managed_gitlab_repository(self.task) as prepared:
            yield prepared[0]

    def _run_semgrep(self, root, diffs):
        paths = [item.get("path") for item in diffs if item.get("path") and (root / item.get("path")).is_file()]
        if not paths:
            return [], "Semgrep 无可扫描的变更文件"
        scanner_url = os.environ.get("SEMGREP_SCANNER_URL", "").rstrip("/")
        if scanner_url:
            try:
                import requests
                files = [{"path": path, "content": (root / path).read_text(encoding="utf-8", errors="replace")[:2_000_000]} for path in paths]
                response = requests.post(f"{scanner_url}/scan", json={"files": files}, timeout=200)
                response.raise_for_status()
                payload = response.json()
            except (OSError, ValueError, requests.RequestException) as exc:
                return [], f"Semgrep扫描器不可用：{exc}"
            return self._normalize_semgrep(payload)
        executable = shutil.which("semgrep")
        if not executable:
            return [], "Semgrep 未安装，已跳过"
        rules = Path(__file__).with_name("rules") / "semgrep.yml"
        try:
            result = subprocess.run(
                [executable, "scan", "--config", str(rules), "--json", "--metrics", "off", "--disable-version-check", *paths],
                cwd=root, text=True, capture_output=True, timeout=180,
            )
            payload = json.loads(result.stdout or "{}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            return [], f"Semgrep 未完成：{exc}"
        return self._normalize_semgrep(payload)

    @staticmethod
    def _normalize_semgrep(payload):
        severity_map = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
        findings = []
        for item in (payload.get("results") or [])[:200]:
            extra = item.get("extra") or {}
            line = (item.get("start") or {}).get("line")
            rule_id = item.get("check_id") or "unknown"
            findings.append({
                "key": f"semgrep:{item.get('path')}:{line}:{rule_id}",
                "change": f"Semgrep：{extra.get('message') or rule_id}", "file": item.get("path", ""),
                "severity": severity_map.get(str(extra.get("severity") or "WARNING").upper(), "medium"),
                "source": "static_scan", "confidence": 1.0, "verified": True,
                "line_start": line, "rule_id": rule_id,
                "evidence": ((extra.get("lines") or "")[:2000] or rule_id),
                "impact": f"确定性规则 {rule_id} 命中，需结合触发路径确认实际影响",
            })
        errors = payload.get("errors") or []
        note = f"已执行 Semgrep，发现 {len(findings)} 条规则命中"
        if errors:
            note += f"，另有 {len(errors)} 条扫描错误"
        return findings, note

    def run_static_checks(self, diffs):
        """运行仓库已安装的只读前端检查器；未安装时明确跳过而不伪造结果。"""
        try:
            with self._repository_root() as root:
                return self._run_static_checks_in_root(root, diffs)
        except Exception as exc:
            return [], [f"静态检查无法准备仓库：{exc}"]

    def _run_static_checks_in_root(self, root, diffs):
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
        semgrep_findings, semgrep_note = self._run_semgrep(root, diffs)
        findings.extend(semgrep_findings)
        notes.append(semgrep_note)
        return findings, notes
