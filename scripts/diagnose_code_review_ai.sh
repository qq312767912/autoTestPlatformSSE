#!/usr/bin/env bash
# Read-only diagnosis. Does not read reviewed repositories or dump credentials.
set -eu
CONTAINER="${1:-wharttest-backend}"
OUT="${2:-code-review-ai-diagnosis.log}"
umask 077
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "容器不存在或无 Docker 权限：$CONTAINER" >&2
  exit 1
fi
{
  echo '=== 诊断时间 ==='
  date -Iseconds
  echo '=== 容器版本（不输出环境变量） ==='
  docker inspect --format 'name={{.Name}} image={{.Config.Image}} image_id={{.Image}} started={{.State.StartedAt}}' "$CONTAINER"
  echo '=== 平台源文件静态检查（不输出源码） ==='
  docker exec -i "$CONTAINER" /opt/venv/bin/python - <<'PY'
import ast, hashlib, pathlib
target = '_repository_context_for_prompt'
roots = [pathlib.Path('/app/code_analysis')]
for root in roots:
    if not root.is_dir():
        print('MISSING_DIRECTORY', root)
        continue
    definitions = []
    references = []
    for path in sorted(root.rglob('*.py')):
        if any(x in path.parts for x in ('migrations', '__pycache__', '.git')):
            continue
        raw = path.read_bytes()
        try:
            tree = ast.parse(raw, filename=str(path))
        except Exception as exc:
            print('PARSE_ERROR', path, type(exc).__name__)
            continue
        relevant = target.encode() in raw
        if relevant or path.name in ('services.py', 'tasks.py'):
            print('FILE', path, 'sha256=' + hashlib.sha256(raw).hexdigest(), 'bytes=' + str(len(raw)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
                definitions.append((str(path), node.lineno))
            if isinstance(node, ast.Name) and node.id == target and isinstance(node.ctx, ast.Load):
                references.append((str(path), node.lineno))
                print('BARE_REFERENCE', path, node.lineno)
            if isinstance(node, ast.Attribute) and node.attr == target:
                print('ATTRIBUTE_REFERENCE', path, node.lineno)
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == target:
                        print('IMPORT_BINDING', path, node.lineno, node.module, alias.name)
        if relevant:
            bound = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    bound.update(a.asname or a.name.split('.')[0] for a in node.names)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for item in targets:
                        bound.update(n.id for n in ast.walk(item) if isinstance(n, ast.Name))
            print('TOP_LEVEL_BINDING', path, target in bound)
    for path, line in definitions:
        print('DEFINITION', path, line)
    print('SUMMARY', 'definitions=' + str(len(definitions)), 'bare_references=' + str(len(references)))
    if references and not definitions:
        print('WARNING: 本目录存在调用但没有函数定义；需结合 IMPORT_BINDING 确认是否缺少导入或配套补丁。')
print('NOTE: 静态检查不能证明运行时绑定正确；以同次任务 traceback 为准。')
PY
  echo '=== 进程与 Supervisor ==='
  docker exec "$CONTAINER" sh -c 'supervisorctl status 2>/dev/null || true'
  echo '=== AI 失败相关日志（仅提取固定错误信息，不输出原始日志或审查内容） ==='
  docker exec -i "$CONTAINER" /opt/venv/bin/python - <<'PY'
from pathlib import Path
patterns = {
 'missing_repository_context': "name '_repository_context_for_prompt' is not defined",
 'ai_analysis_failed': 'AI分析失败',
 'upstream_unavailable': 'upstream_unavailable',
 'timeout': 'TimeoutError',
}
for name in ('worker_err.log', 'worker_out.log', 'celery.log', 'django.log'):
    p = Path('/app/data/logs') / name
    if not p.is_file():
        print('LOG_MISSING', name)
        continue
    # Bound memory and output; never print original log lines.
    with p.open('rb') as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - 8 * 1024 * 1024))
        lines = f.read().decode('utf-8', errors='replace').splitlines()
    print('LOG', name, 'bytes=', size, 'tail_limit=8MiB')
    for label, needle in patterns.items():
        hits = [i for i, line in enumerate(lines, 1) if needle in line]
        print('ERROR_COUNT', label, len(hits), 'tail_line_positions=', hits[-10:])
print('NOTE: 错误计数可能包含历史任务，不能单凭计数归因当前任务。')
PY
  echo '=== 完成 ==='
  echo '本脚本没有修改配置、重启服务、重跑任务或导出被审查代码。'
} > "$OUT" 2>&1
echo "诊断结果：$OUT"
