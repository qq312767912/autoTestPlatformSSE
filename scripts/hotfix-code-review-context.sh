#!/usr/bin/env bash
# 仅适用于内网 wharttest-backend 精确版本；先在目标容器导入预检，失败保留现场并回滚。
set -euo pipefail

[ "${1:-}" = --apply ] || { echo '用法：bash hotfix-code-review-context.sh --apply [容器名]' >&2; exit 2; }
container="${2:-wharttest-backend}"
expected_image='sha256:7b982b8131bfd86cd7c71274321fbffc3fa6b060f58c413ad796630375a658fe'
expected_source='492f820e1495a884fd27d93e8e923be41ce8a91b0e42ef91cd41adee2fa7a07f'
source_path='/app/code_analysis/services.py'
case "$container" in *[!a-zA-Z0-9_.-]*|'') echo '非法容器名' >&2; exit 2;; esac
command -v docker >/dev/null || { echo '未找到 Docker' >&2; exit 2; }
command -v python3 >/dev/null || { echo '未找到 Python 3' >&2; exit 2; }
image="$(docker inspect --type container --format '{{.Image}}' "$container")"
[ "$image" = "$expected_image" ] || { echo "镜像不匹配：$image；拒绝修改" >&2; exit 2; }
[ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ] || { echo '容器未运行' >&2; exit 2; }
[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}healthy{{end}}' "$container")" = healthy ] || { echo '执行前容器未处于健康状态；拒绝热修复' >&2; exit 2; }

backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/whart-context-hotfix.XXXXXX")"
chmod 700 "$backup_dir"
docker cp "$container:$source_path" "$backup_dir/services.py.orig"
actual_source="$(python3 - "$backup_dir/services.py.orig" <<'PY'
import hashlib,sys
print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())
PY
)"
[ "$actual_source" = "$expected_source" ] || { echo "源码哈希不匹配：$actual_source；拒绝修改。备份：$backup_dir" >&2; exit 2; }

python3 - "$backup_dir/services.py.orig" "$backup_dir/services.py" <<'PY'
import ast
import pathlib
import sys

original = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
tree = ast.parse(original)
name = '_repository_context_for_prompt'
if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name for node in tree.body):
    raise SystemExit('函数已存在；拒绝重复修补')
calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name]
if len(calls) != 1:
    raise SystemExit(f'预期恰好一个调用，实际 {len(calls)} 个；拒绝修补')
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
# 插入于首个模块级函数前；不调整其它代码或 import。
first = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
if first is None:
    raise SystemExit('找不到模块级函数；拒绝修补')
lines = original.splitlines(keepends=True)
insert_line = min([first.lineno] + [item.lineno for item in first.decorator_list])
patched = ''.join(lines[:insert_line-1]) + snippet + ''.join(lines[insert_line-1:])
ast.parse(patched)
pathlib.Path(sys.argv[2]).write_text(patched, encoding='utf-8')
PY

python3 -m py_compile "$backup_dir/services.py"
cat > "$backup_dir/rollback.txt" <<EOF
容器=$container
原镜像=$expected_image
原源码SHA256=$expected_source
原文件=$backup_dir/services.py.orig
回滚命令：docker cp '$backup_dir/services.py.orig' '$container:$source_path' && docker restart '$container'
EOF

changed=0
restarted=0
capture_failure() {
  docker inspect "$container" > "$backup_dir/failed-container.json" 2>&1 || true
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}开始={{.Start}} 结束={{.End}} 退出码={{.ExitCode}} 输出={{.Output}}{{println}}{{end}}{{end}}' "$container" > "$backup_dir/failed-health.txt" 2>&1 || true
  docker logs --tail 500 "$container" > "$backup_dir/failed-logs.txt" 2>&1 || true
  echo "失败现场已保存：$backup_dir（可能含凭据，请脱敏后分享）" >&2
}
wait_healthy() {
  local phase="$1" attempt status
  for attempt in $(seq 1 90); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo missing)"
    if [ "$status" = healthy ]; then
      echo "$phase：容器恢复健康（约 $(((attempt - 1) * 2)) 秒）"
      return 0
    fi
    if [ $((attempt % 15)) -eq 0 ]; then echo "$phase：仍为 $status（约 $((attempt * 2)) 秒）"; fi
    sleep 2
  done
  echo "$phase：等待 3 分钟后仍未健康" >&2
  return 1
}
rollback() {
  local original_status=$?
  trap - ERR INT TERM
  if [ "$changed" -eq 1 ]; then
    [ "$restarted" -eq 0 ] || capture_failure
    echo '验证失败，自动恢复原文件' >&2
    if docker cp "$backup_dir/services.py.orig" "$container:$source_path"; then
      if [ "$restarted" -eq 1 ]; then
        if docker restart "$container" >/dev/null && wait_healthy '回滚验证'; then
          echo "原文件已恢复，容器健康；备份目录：$backup_dir" >&2
        else
          echo "紧急告警：回滚后容器仍未健康；请检查 $backup_dir/rollback.txt" >&2
        fi
      else
        echo '原文件已恢复；此前未重启容器，在线服务保持原状态' >&2
      fi
    else
      echo "紧急告警：自动回滚命令失败；请检查 $backup_dir/rollback.txt" >&2
    fi
  fi
  exit "${original_status:-1}"
}
trap rollback ERR INT TERM
changed=1
docker cp "$backup_dir/services.py" "$container:$source_path"
docker exec -i "$container" /opt/venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wharttest_django.settings')
import django
django.setup()
from code_analysis import services
fn = services._repository_context_for_prompt
assert callable(fn)
assert isinstance(fn({'items': [], 'graph_context': {}}), dict)
assert fn([])['items'] == []
print('目标容器补丁导入预检通过；尚未重启')
PY
restarted=1
docker restart "$container" >/dev/null
healthy=0
for attempt in $(seq 1 150); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
  if [ "$status" = healthy ] || [ "$status" = running -a "$(docker inspect --format '{{if .State.Health}}yes{{else}}no{{end}}' "$container")" = no ]; then healthy=1; break; fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]; then
    echo "第 $attempt 次检查：$status；容器未正常运行" >&2
    false
  fi
  if [ $((attempt % 15)) -eq 0 ]; then echo "等待容器健康检查：$status（已等待约 $((attempt * 2)) 秒）"; fi
  sleep 2
done
[ "$healthy" -eq 1 ] || { echo '容器未达到健康状态' >&2; false; }
docker exec -i "$container" /opt/venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wharttest_django.settings')
import django
django.setup()
from code_analysis import services
fn = services._repository_context_for_prompt
assert callable(fn)
assert isinstance(fn({'items': [], 'graph_context': {}}), dict)
assert fn([])['items'] == []
print('函数导入和最小行为检查通过')
PY
changed=0
trap - ERR INT TERM
echo "热修复完成；备份与回滚说明：$backup_dir"
echo '注意：此补丁只在当前容器文件层生效；容器重建会丢失。下一步应基于原镜像制作持久化镜像并固定部署配置。'
