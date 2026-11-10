#!/usr/bin/env bash
# 在内网主机上运行：基于当前 Backend 镜像构建定点修复镜像，不重启或修改运行中的容器。
set -euo pipefail

container="${1:-wharttest-backend}"
expected_image='sha256:7b982b8131bfd86cd7c71274321fbffc3fa6b060f58c413ad796630375a658fe'
expected_source='492f820e1495a884fd27d93e8e923be41ce8a91b0e42ef91cd41adee2fa7a07f'
expected_tag='wharttest-250-backend:update-b6613a39-v2.8-r5-code-review-backend-hostcfg1-arm64'
fixed_tag='wharttest-250-backend:update-b6613a39-v2.8-r5-code-review-contextfix1-arm64'

case "$container" in *[!a-zA-Z0-9_.-]*|'') echo '非法容器名' >&2; exit 2;; esac
command -v docker >/dev/null || { echo '未找到 Docker' >&2; exit 2; }
command -v python3 >/dev/null || { echo '未找到 Python 3' >&2; exit 2; }

actual_image="$(docker inspect --type container --format '{{.Image}}' "$container")"
actual_tag="$(docker inspect --type container --format '{{.Config.Image}}' "$container")"
actual_health="$(docker inspect --type container --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
[ "$actual_image" = "$expected_image" ] || { echo "原镜像 ID 不匹配：$actual_image" >&2; exit 2; }
[ "$actual_tag" = "$expected_tag" ] || { echo "原镜像标签不匹配：$actual_tag" >&2; exit 2; }
[ "$actual_health" = healthy ] || { echo "当前容器不健康：$actual_health；拒绝构建" >&2; exit 2; }
[ "$(docker image inspect --format '{{.Id}}' "$expected_tag")" = "$expected_image" ] || { echo '原镜像标签已被改指向其他镜像' >&2; exit 2; }
if docker image inspect "$fixed_tag" >/dev/null 2>&1; then
  echo "目标标签已存在：$fixed_tag；拒绝覆盖" >&2
  exit 2
fi

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/whart-context-image.XXXXXX")"
chmod 700 "$build_dir"
echo "构建工作目录：$build_dir"
docker cp "$container:/app/code_analysis/services.py" "$build_dir/services.py.orig"
actual_source="$(python3 - "$build_dir/services.py.orig" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
[ "$actual_source" = "$expected_source" ] || { echo "原源码 SHA256 不匹配：$actual_source" >&2; exit 2; }

python3 - "$build_dir/services.py.orig" "$build_dir/services.py" <<'PY'
import ast
import pathlib
import sys

original = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
tree = ast.parse(original)
name = '_repository_context_for_prompt'
if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body):
    raise SystemExit('原文件已包含目标函数，拒绝重复修复')
calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]
if len(calls) != 1:
    raise SystemExit(f'目标函数调用数不是 1：{len(calls)}')
snippet = '''\
def _repository_context_for_prompt(context):
    """Bound repository context without truncating the separately supplied Diff."""
    context = context if isinstance(context, dict) else {}
    graph = context.get("graph_context")
    graph = graph if isinstance(graph, dict) else {}
    graph_prompt = {
        "status": graph.get("status", "unavailable"),
        "affected_files": (graph.get("affected_files") or [])[:40],
        "callers": (graph.get("callers") or [])[:30],
        "related_tests": (graph.get("related_tests") or [])[:30],
        "affected_flows": (graph.get("affected_flows") or [])[:10],
        "test_gaps": (graph.get("test_gaps") or [])[:30],
    }
    items = []
    for item in (context.get("items") or [])[:20]:
        if not isinstance(item, dict):
            continue
        items.append({
            "path": item.get("path"),
            "declarations": (item.get("declarations") or [])[:12],
            "related_code": (item.get("related_code") or [])[:12],
            "ast_chunks": [
                {
                    "kind": chunk.get("kind"),
                    "start_line": chunk.get("start_line"),
                    "end_line": chunk.get("end_line"),
                    "content": str(chunk.get("content") or "")[:2500],
                }
                for chunk in (item.get("ast_chunks") or [])[:8]
                if isinstance(chunk, dict)
            ],
            "source": str(item.get("source") or "")[:3000],
        })
    return {"graph_context": graph_prompt, "items": items, "parser": context.get("parser")}

'''
first = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
if first is None:
    raise SystemExit('找不到模块级函数')
insert_line = min([first.lineno] + [n.lineno for n in first.decorator_list])
lines = original.splitlines(keepends=True)
patched = ''.join(lines[:insert_line - 1]) + snippet + ''.join(lines[insert_line - 1:])
ast.parse(patched)
pathlib.Path(sys.argv[2]).write_text(patched, encoding='utf-8')
PY

python3 -m py_compile "$build_dir/services.py"
printf 'FROM %s\nCOPY services.py /app/code_analysis/services.py\n' "$expected_tag" > "$build_dir/Dockerfile"
docker build --network none --pull=false -t "$fixed_tag" "$build_dir"

# 只在独立临时容器中验证目标 Python 能编译并运行新增函数，不连接数据库/外网。
docker run --rm --network none --entrypoint /opt/venv/bin/python "$fixed_tag" -c '
import ast, pathlib
p = pathlib.Path("/app/code_analysis/services.py")
tree = ast.parse(p.read_text(encoding="utf-8"))
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_repository_context_for_prompt"]
assert len(nodes) == 1, len(nodes)
ns = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(p), "exec"), ns)
fn = ns["_repository_context_for_prompt"]
assert fn(None)["items"] == []
assert fn({"items": [{"path": "a.py", "source": "x"}], "graph_context": {"status": "ready"}})["items"][0]["path"] == "a.py"
print("目标运行时语法与函数行为验证通过")
'

docker image inspect --format '修复镜像={{.RepoTags}} ID={{.Id}} 架构={{.Os}}/{{.Architecture}}' "$fixed_tag"
[ "$(docker inspect --type container --format '{{.Image}}' "$container")" = "$expected_image" ] || { echo '运行中容器镜像发生变化，请立即检查' >&2; exit 1; }
[ "$(docker inspect --type container --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")" = healthy ] || { echo '运行中容器健康状态发生变化，请立即检查' >&2; exit 1; }
echo '修复镜像已构建并预检；运行中 Backend 未修改、未重启。请勿自行切换，下一步需先准备独立部署与回滚。'
