#!/usr/bin/env bash
# 只读：检查代码审查热修复回滚后的后端容器状态与健康检查原因。
set -u

container="${1:-wharttest-backend}"
case "$container" in *[!a-zA-Z0-9_.-]*|'') echo '非法容器名' >&2; exit 2;; esac
command -v docker >/dev/null 2>&1 || { echo '未找到 Docker' >&2; exit 2; }
if ! docker info >/dev/null 2>&1; then
  echo '无法连接 Docker；请在容器所在主机运行，并检查 Docker socket 权限。' >&2
  exit 2
fi
if ! docker inspect --type container "$container" >/dev/null 2>&1; then
  echo "未找到容器 $container。可选容器：" >&2
  docker ps -a --format '{{.Names}}  {{.Image}}  {{.Status}}' >&2
  exit 2
fi

echo '[1/4] 容器状态与启动时间'
docker inspect --format '名称={{.Name}} 镜像={{.Config.Image}} 镜像ID={{.Image}} 状态={{.State.Status}} 退出码={{.State.ExitCode}} 重启次数={{.RestartCount}} 启动={{.State.StartedAt}} 健康={{if .State.Health}}{{.State.Health.Status}}{{else}}未配置{{end}}' "$container"
echo '[2/4] 健康检查命令与最近结果'
docker inspect --format '{{if .Config.Healthcheck}}命令={{json .Config.Healthcheck.Test}} 间隔={{.Config.Healthcheck.Interval}} 超时={{.Config.Healthcheck.Timeout}} 启动宽限={{.Config.Healthcheck.StartPeriod}}{{else}}未配置健康检查{{end}}' "$container"
docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}开始={{.Start}} 结束={{.End}} 退出码={{.ExitCode}} 输出={{printf "%.500s" .Output}}{{println}}{{end}}{{end}}' "$container"
echo '[3/4] 近期启动或健康相关日志（最近 300 行中筛选，最多 80 行）'
docker logs --tail 300 "$container" 2>&1 | grep -Ei 'error|exception|traceback|health|starting|started|ready|listen|worker|supervisor|gunicorn|uvicorn|celery|mcp|failed' | sed -E '/(API[ _-]?Key|token|secret|password)/I s/.*/[已隐藏可能含凭据的日志行]/' | tail -n 80 || true
echo '[4/4] 原源码是否已恢复'
if [ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ]; then
  docker exec "$container" python -c 'import hashlib,pathlib; p=pathlib.Path("/app/code_analysis/services.py"); s=p.read_bytes(); print("SHA256=", hashlib.sha256(s).hexdigest()); print("函数定义次数=", s.count(b"def _repository_context_for_prompt(")); print("函数调用次数=", s.count(b"_repository_context_for_prompt(repository_context)"))' || true
else
  echo '容器未运行，无法读取源码'
fi
echo '请在分享输出前检查并遮盖内部地址、令牌和任务标识。脚本未修改容器。'
