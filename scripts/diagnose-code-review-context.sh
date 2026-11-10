#!/usr/bin/env bash
# 只读诊断：代码审查 AI 阶段提示 _repository_context_for_prompt 未定义。
set -u

if ! command -v docker >/dev/null 2>&1; then echo "未找到 docker" >&2; exit 2; fi
docker_error="$(docker info --format '{{.ServerVersion}}' 2>&1)"
if [ "$?" -ne 0 ]; then
  echo "Docker 服务不可访问：$docker_error" >&2
  echo "请在运行容器的主机上执行，并检查 Docker 是否启动及当前用户的 socket 权限。" >&2
  exit 2
fi
container="${1:-}"
if [ -z "$container" ]; then
  candidates="$(docker ps --format '{{.Names}}' | grep -E '(^|-)backend$' || true)"
  count="$(printf '%s\n' "$candidates" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$count" -eq 1 ]; then
    container="$candidates"
    echo "自动选择后端容器：$container"
  else
    echo "检测到 $count 个运行中的后端容器，请指定名称。可选容器：" >&2
    docker ps --format '{{.Names}}  {{.Image}}  {{.Status}}' >&2
    echo "用法：bash $0 <后端容器名>" >&2
    exit 2
  fi
fi
case "$container" in
  *[!a-zA-Z0-9_.-]*|'') echo "容器名仅允许字母、数字、点、下划线和连字符" >&2; exit 2 ;;
esac
if ! docker inspect --type container "$container" >/dev/null 2>&1; then
  echo "容器 $container 不存在；当前容器：" >&2
  docker ps -a --format '{{.Names}}  {{.Image}}  {{.Status}}' >&2
  exit 2
fi
if [ "$(docker inspect --format '{{.State.Running}}' "$container")" != "true" ]; then
  echo "容器 $container 存在但未运行；不能执行容器内检查。" >&2
  exit 2
fi

echo "[1/4] 容器身份与健康状态"
docker inspect --format '名称={{.Name}} 镜像={{.Config.Image}} 镜像ID={{.Image}} 启动时间={{.State.StartedAt}} 状态={{.State.Status}} 健康={{if .State.Health}}{{.State.Health.Status}}{{else}}未配置{{end}}' "$container"
echo "[2/4] services.py 的挂载来源"
docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis"}}{{.Source}} -> {{.Destination}} (RW={{.RW}}){{end}}{{end}}' "$container"
echo "[3/4] 容器内当前 Python 模块与源码"
docker exec -i "$container" python - <<'PY'
import hashlib
import inspect
import os
import sys

try:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
    import django
    django.setup()
    import code_analysis.services as service
except Exception as exc:
    print("模块导入失败:", type(exc).__name__, str(exc)[:300])
    sys.exit(3)

name = "_repository_context_for_prompt"
path = inspect.getsourcefile(service) or getattr(service, "__file__", "")
print("模块路径:", path)
print("Python:", sys.version.split()[0])
print("模块内有函数:", callable(getattr(service, name, None)))
stage = getattr(service, "_analysis_stage", None)
print("分析阶段有函数:", bool(stage and name in stage.__globals__))
if stage:
    print("分析阶段模块:", stage.__module__)
if path and os.path.isfile(path):
    with open(path, "rb") as source:
        data = source.read()
    print("当前源码 SHA256:", hashlib.sha256(data).hexdigest())
    print("定义文本次数:", data.count(b"def _repository_context_for_prompt("))
    print("调用文本次数:", data.count(b"_repository_context_for_prompt(repository_context)"))
    lines = data.decode("utf-8", "replace").splitlines()
    for number, line in enumerate(lines, 1):
        if "def _repository_context_for_prompt(" in line or "_repository_context_for_prompt(repository_context)" in line:
            print("位置:", number, line.strip()[:140])
PY
probe_status=$?
if [ "$probe_status" -ne 0 ]; then echo "容器内模块探测失败，退出码 $probe_status" >&2; fi

echo "[4/4] 最近 24 小时相关错误（最多 30 行，可能含任务 ID；分享前请脱敏）"
docker logs --since 24h "$container" 2>&1 | grep -E '(_repository_context_for_prompt|AI分析失败|代码审查任务执行失败)' | tail -n 30 || true

echo "判读：定义/调用次数不匹配=镜像或挂载源码缺失；源码完整但当前模块缺函数=进程加载版本不一致；两者均存在=检查发生错误时的容器/任务日志与部署时间。"
exit "$probe_status"
