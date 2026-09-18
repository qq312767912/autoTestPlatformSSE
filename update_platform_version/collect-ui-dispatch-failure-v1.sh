#!/usr/bin/env bash
set -u

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/ui-dispatch-failure-${STAMP}.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

section() {
  echo
  echo "========== $1 =========="
}

run() {
  echo "+ $*"
  "$@" 2>&1 || echo "[WARN] 命令失败，退出码: $?"
}

echo "UI 用例任务下发失败采集"
echo "采集时间: $(date '+%F %T %z')"

section "容器状态"
run docker ps -a --filter name=wharttest --format 'table {{.Names}}\t{{.Status}}\t{{.Networks}}'

section "后端 Supervisor 状态"
run docker exec wharttest-backend supervisorctl status

section "Django 标准输出日志"
run docker exec wharttest-backend sh -lc 'tail -n 1200 /var/log/django_out.log'

section "Django 错误日志"
run docker exec wharttest-backend sh -lc 'tail -n 1200 /var/log/django_err.log'

section "Supervisor 日志"
run docker exec wharttest-backend sh -lc 'tail -n 300 /var/log/supervisord.log'

section "执行器最近 15 分钟日志"
for name in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  echo
  echo "----- $name -----"
  run docker logs --timestamps --since 15m "$name"
done

section "执行器域名与目标站点检查"
for name in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  echo
  echo "----- $name -----"
  run docker exec "$name" getent hosts www.test.sse.com.cn
  run docker exec "$name" python -c 'import urllib.request; r=urllib.request.urlopen("http://www.test.sse.com.cn", timeout=15); print("HTTP", r.status, r.url)'
done

section "结束"
echo "请将本日志完整提供。"
echo "日志文件: $LOG_FILE"
