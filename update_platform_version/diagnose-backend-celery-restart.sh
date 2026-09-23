#!/usr/bin/env bash
# 只读采集 Backend/Celery 反复重启的证据，不修改容器和数据。
set -uo pipefail

CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
OUTPUT="${1:-docker-backend-celery-$(date '+%Y%m%d-%H%M%S').log}"

exec > >(tee "$OUTPUT") 2>&1
section() { printf '\n===== %s =====\n' "$1"; }

section "容器状态"
docker inspect "$CONTAINER" --format \
  'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} started={{.State.StartedAt}}' || true
docker stats --no-stream "$CONTAINER" --format 'cpu={{.CPUPerc}} memory={{.MemUsage}} pids={{.PIDs}}' || true

section "Supervisor 与 Celery 配置"
docker exec "$CONTAINER" sh -c '
  grep -nE "command=(uvicorn|celery)" /app/supervisord.conf || true
  echo "当前进程 cmdline："
  for cmdline in /proc/[0-9]*/cmdline; do
    command_line=$(tr "\000" " " < "$cmdline" 2>/dev/null || true)
    case "$command_line" in
      "/opt/venv/bin/python /opt/venv/bin/celery "*|"/opt/venv/bin/python /opt/venv/bin/uvicorn "*|"/usr/bin/python3 /usr/bin/supervisord "*) printf "%s %s\n" "${cmdline#/proc/}" "$command_line" ;;
    esac
  done
' || true

section "Redis 连通性"
docker exec wharttest-redis redis-cli ping || true
docker exec "$CONTAINER" python -c \
  'import redis; r=redis.Redis.from_url("redis://redis:6379/0", socket_timeout=5); print("redis_python_ping=", r.ping())' || true

section "内存与 OOM 计数"
docker exec "$CONTAINER" sh -c '
  for file in /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.current /sys/fs/cgroup/pids.max; do
    echo "--- $file"; cat "$file" 2>/dev/null || true
  done
' || true

section "最近 Docker/Supervisor 日志"
docker logs --timestamps --tail 500 "$CONTAINER" || true

section "Celery stdout"
docker exec "$CONTAINER" sh -c 'tail -n 500 /app/data/logs/worker_out.log' || true

section "Celery stderr"
docker exec "$CONTAINER" sh -c 'tail -n 500 /app/data/logs/worker_err.log' || true

section "40 秒 PID 变化"
for second in $(seq 0 2 40); do
  printf '%s second=%s ' "$(date '+%F %T')" "$second"
  docker exec "$CONTAINER" sh -c '
    found=0
    for cmdline in /proc/[0-9]*/cmdline; do
      command_line=$(tr "\000" " " < "$cmdline" 2>/dev/null || true)
      case "$command_line" in
        "/opt/venv/bin/python /opt/venv/bin/celery -A wharttest_django -b redis://redis:6379/0 worker "*)
          printf "pid=%s cmd=%s\n" "$(basename "$(dirname "$cmdline")")" "$command_line"
          found=1
          break
          ;;
      esac
    done
    [ "$found" = 1 ] || echo "pid=missing"
  ' || true
  sleep 2
done

section "采集结束"
echo "请返回日志文件：$OUTPUT"
