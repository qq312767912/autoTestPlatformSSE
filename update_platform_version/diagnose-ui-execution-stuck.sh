#!/usr/bin/env bash
# 采集“UI 用例点击执行后一直卡住”的完整证据。只读，不重启容器、不修改数据。
set -u

PLATFORM_DIR="${1:-/projects/ai-test-platform}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${PLATFORM_DIR}/update_platform_version/ui-execution-stuck-${STAMP}.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n========== %s ==========\n' "$1"; }
run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  "$@" 2>&1 || printf '[WARN] 命令失败，退出码: %s\n' "$?"
}

echo "UI 用例执行卡住诊断"
echo "采集时间: $(date '+%F %T %z')"

section "容器、镜像与资源"
run docker ps -a --filter name=wharttest --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'
run docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}'
run df -h "${PLATFORM_DIR}"
for name in wharttest-backend wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  run docker inspect "$name" --format 'name={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}} started={{.State.StartedAt}}'
done

section "Backend Supervisor 与进程"
run docker exec wharttest-backend supervisorctl status
run docker exec wharttest-backend ps -ef

section "Backend UI 任务数据库状态（最近 20 条）"
run docker exec -i wharttest-backend /opt/venv/bin/python /app/manage.py shell <<'PY'
from ui_automation.models import UiBatchExecutionRecord, UiExecutionRecord, UiTestCase

print("-- batches --")
for x in UiBatchExecutionRecord.objects.order_by("-id")[:20]:
    print({
        "id": x.id, "name": x.name, "status": x.status,
        "total": x.total_cases, "passed": x.passed_cases, "failed": x.failed_cases,
        "start": str(x.start_time), "end": str(x.end_time), "created": str(x.created_at),
    })
print("-- execution records --")
for x in UiExecutionRecord.objects.select_related("test_case").order_by("-id")[:20]:
    print({
        "id": x.id, "batch_id": x.batch_id, "case_id": x.test_case_id,
        "case": getattr(x.test_case, "name", ""), "status": x.status,
        "start": str(x.start_time), "end": str(x.end_time),
        "error": (x.error_message or "")[:500], "created": str(x.created_at),
    })
print("-- cases left in running state --")
for x in UiTestCase.objects.filter(status=1).order_by("-id")[:50]:
    print({"id": x.id, "name": x.name, "project_id": x.project_id, "status": x.status})
PY

section "Redis 执行器 slot 租约"
run docker exec wharttest-redis redis-cli HGETALL ui_auto:slot_leases

section "Backend 持久化日志"
for file in django_out.log django_err.log worker_out.log worker_err.log beat_out.log beat_err.log supervisord.log; do
  echo "----- /app/data/logs/$file -----"
  run docker exec wharttest-backend sh -lc "test -f /app/data/logs/$file && tail -n 1200 /app/data/logs/$file || true"
done

section "执行器环境、连通性与浏览器启动"
for name in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  echo "----- $name -----"
  run docker exec "$name" sh -lc 'env | grep -E "^(WHARTTEST_ACTUATOR_(ID|NAME|WS_URL|API_URL|HEADLESS|BROWSER_TYPE)|VISION_MCP_URL)=" | sort'
  run docker exec "$name" python -c 'import socket; print("backend", socket.gethostbyname("backend")); s=socket.create_connection(("backend",8000),5); print("backend:8000 TCP OK"); s.close()'
  run docker exec "$name" python -c 'import urllib.request; r=urllib.request.urlopen("http://backend:8000/api/auth/login-key/", timeout=10); print("Backend HTTP", r.status)'
  run docker exec "$name" sh -lc 'test -x /usr/bin/chromium-browser && /usr/bin/chromium-browser --version'
  run docker exec "$name" python -c 'import asyncio; from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True, executable_path="/usr/bin/chromium-browser", args=["--no-sandbox","--disable-dev-shm-usage"])
        page=await b.new_page(); await page.goto("about:blank", timeout=15000); print("Playwright Chromium OK", await page.title()); await b.close()
asyncio.run(main())'
  echo "----- $name logs (last 30m) -----"
  run docker logs --timestamps --since 30m "$name"
done

section "关键错误摘要"
run docker logs --since 30m wharttest-backend
for name in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  run docker logs --since 30m "$name"
done

section "结束"
echo "请将该日志完整传回：$LOG_FILE"
