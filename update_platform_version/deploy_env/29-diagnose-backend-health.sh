#!/usr/bin/env bash
set -uo pipefail

# ============================================================================
# Backend 健康检查诊断：区分「探针超时误报」与「应用真的起不来」
# ============================================================================
# 什么时候用：
#   04-deploy.sh / 19/20/22/23/28-apply-*.sh 以「Backend 状态异常：unhealthy」结束时，
#   或 docker ps 里 wharttest-backend 长时间显示 (unhealthy) 时。
#
# 背景（本平台的两处坑，不了解很容易误判）：
#   1) 镜像自带的健康探针是
#        python -c "urllib.request.urlopen('http://127.0.0.1:8000/admin/login/')"
#      单次超时 10s、总预算约 130s（start_period 40s + 重试 3×30s）。
#      而 Django(ASGI, uvicorn --workers=1) 直到「首个请求」才 import 整个应用，
#      冷启动偶尔超过该预算 —— 于是「接口其实能用，但 docker ps 显示 unhealthy」。
#      探针下一次成功后会自动回到 healthy，所以第一个动作永远是「再看一眼当前状态」。
#   2) django / celery 的日志被 supervisord 写到容器内 /app/data/logs/*.log
#      （即宿主机 offline-images/data/logs/），docker logs 里几乎看不到应用报错。
#      因此排查必须读那几个文件，本脚本已包含。
#
# 用法：
#   bash 29-diagnose-backend-health.sh
#
# 可用环境变量覆盖：
#   BASE_COMPOSE        内网基础 compose
#   BACKEND_CONTAINER   后端容器名（默认 wharttest-backend）
#   BACKEND_URL         后端地址（默认 http://127.0.0.1:8912）
#   EXPECTED_BACKEND_IMAGE  期望的镜像 tag
# ============================================================================

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$UPDATE_DIR/logs"
LOG_FILE="$LOG_DIR/backend-health-$(date '+%Y%m%d-%H%M%S').log"
BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8912}"
EXPECTED_IMAGE="${EXPECTED_BACKEND_IMAGE:-wharttest-250-backend:update-d595a628-review-fix-r5-arm64}"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
ind() { sed 's/^/    /' 2>/dev/null || true; }

echo "Backend 健康检查诊断日志：$LOG_FILE"
echo "采集时间：$(date '+%F %T %z')"

# 容器内 python：后端镜像在 /opt/venv，个别版本只有 PATH 里的 python
PYBIN="$(docker exec "$BACKEND_CONTAINER" sh -c 'test -x /opt/venv/bin/python && echo /opt/venv/bin/python || echo python' 2>/dev/null || echo python)"
echo "容器内解释器：$PYBIN"

section "1. 结论先行：当前健康状态"
docker inspect "$BACKEND_CONTAINER" --format 'image={{.Config.Image}}
state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}无健康检查{{end}}
restarts={{.RestartCount}} oom={{.State.OOMKilled}} started={{.State.StartedAt}}' 2>&1 || true

section "2. 生效中的健康探针（看预算参数是否被 update.yml 覆盖过）"
docker inspect "$BACKEND_CONTAINER" --format '{{if .Config.Healthcheck}}test={{json .Config.Healthcheck.Test}}
interval={{.Config.Healthcheck.Interval}} timeout={{.Config.Healthcheck.Timeout}} retries={{.Config.Healthcheck.Retries}} start_period={{.Config.Healthcheck.StartPeriod}}{{else}}（无）{{end}}' 2>&1 || true

section "3. 健康探针最近几次结果（误报与否的关键证据）"
docker inspect "$BACKEND_CONTAINER" --format '{{if .State.Health}}{{range .State.Health.Log}}exit={{.ExitCode}}  起={{.Start}}  止={{.End}}
  输出：{{printf "%s" .Output}}
{{end}}{{else}}（镜像未定义健康检查）{{end}}' 2>&1 | head -40 || true

section "4. supervisord 各程序状态"
docker exec "$BACKEND_CONTAINER" sh -c 'supervisorctl -c /app/supervisord.conf status 2>&1 || ps -ef 2>&1 | head -25' 2>&1 || true

section "5. 容器内直接探 HTTP（不经过 Docker 健康检查）"
docker exec "$BACKEND_CONTAINER" "$PYBIN" -c '
import urllib.request, urllib.error
for url in ["http://127.0.0.1:8000/admin/login/"]:
    try:
        r = urllib.request.urlopen(url, timeout=20)
        print("%s -> HTTP %s" % (url, r.status))
    except urllib.error.HTTPError as e:
        print("%s -> HTTP %s" % (url, e.code))
    except Exception as e:
        print("%s -> 未响应：%s: %s" % (url, type(e).__name__, e))
' 2>&1 || true

section "6. 应用代码可导入性 + 路由反解（能过就说明代码没坏）"
docker exec "$BACKEND_CONTAINER" "$PYBIN" -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from django.urls import resolve
try:
    import testcases.urls, testcases.views, testcases.serializers, testcases.permissions
    print("testcases 各模块导入：OK")
except Exception as e:
    print("testcases 模块导入失败：%s: %s" % (type(e).__name__, e))
try:
    m = resolve("/api/testcases/review-llm-config/")
    print("路由反解 /api/testcases/review-llm-config/ -> %s" % m.func.cls.__name__)
except Exception as e:
    print("路由反解失败：%s: %s" % (type(e).__name__, e))
from testcases.models import TestCaseReviewLLMConfig
print("专用配置表单例记录数：%d" % TestCaseReviewLLMConfig.objects.count())
' 2>&1 || true

section "7. Django 错误日志（/app/data/logs/django_err.log 末 80 行）"
docker exec "$BACKEND_CONTAINER" tail -n 80 /app/data/logs/django_err.log 2>&1 || true

section "8. Django 输出日志（/app/data/logs/django_out.log 末 40 行）"
docker exec "$BACKEND_CONTAINER" tail -n 40 /app/data/logs/django_out.log 2>&1 || true

section "9. Celery worker / beat 错误日志（各 20 行）"
docker exec "$BACKEND_CONTAINER" sh -c 'tail -n 20 /app/data/logs/worker_err.log 2>&1; echo "--- beat ---"; tail -n 20 /app/data/logs/beat_err.log 2>&1' 2>&1 || true

section "10. 宿主机侧探活（走 8912 端口映射）"
curl -s -o /dev/null -w '    /admin/login/                  -> HTTP %{http_code}\n' "$BACKEND_URL/admin/login/" 2>&1 || true
curl -s -o /dev/null -w '    /api/testcases/review-llm-config/ -> HTTP %{http_code}（期望 401/403）\n' "$BACKEND_URL/api/testcases/review-llm-config/" 2>&1 || true

section "11. 挂载与迁移现状"
docker inspect "$BACKEND_CONTAINER" --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' 2>&1 \
  | grep -F "/update_platform_version/deploy_env/hotfix/" | head -25 || echo "    （未挂载 hotfix 覆盖层）"
docker exec "$BACKEND_CONTAINER" "$PYBIN" -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
import django
django.setup()
from django.db import connection
tables = set(connection.introspection.table_names())
print("表 testcases_testcasereviewllmconfig 存在：", "testcases_testcasereviewllmconfig" in tables)
with connection.cursor() as cur:
    cur.execute("SELECT name, applied FROM django_migrations WHERE app=%s AND name LIKE %s", ["testcases", "0025%"])
    print("已应用迁移：", cur.fetchall())
' 2>&1 || true

section "12. 磁盘与内存（空间满会让应用无声地挂掉）"
docker exec "$BACKEND_CONTAINER" sh -c 'df -h / /app/data 2>&1; echo "--- mem ---"; free -m 2>&1 | head -3' 2>&1 || true
docker system df 2>&1 | head -5 || true

section "13. 镜像是否为预期版本"
docker inspect "$BACKEND_CONTAINER" --format '当前={{.Config.Image}}  期望='"$EXPECTED_IMAGE"'' 2>&1 || true

section "诊断结束"
echo "请把日志文件发回：$LOG_FILE"
echo
echo "快速判读："
echo "  * 第 5/6 节能通、第 10 节 /admin/login/ 是 200  → 应用正常，unhealthy 只是探针超时误报："
echo "    等一次探针周期（30s）后 docker inspect 会回到 healthy；或叠加 docker-compose.update.yml"
echo "    （已把预算放宽到 start_period 180s / 重试 5 / 单次 30s）后重建 Backend。"
echo "  * 第 6 节报导入/路由失败                → 挂载层代码有问题，看第 7 节 traceback 定位文件。"
echo "  * 第 5 节「未响应：Connection refused」  → uvicorn 未监听 8000，看第 4/7/8 节。"
echo "  * 第 9 节 celery 报错、第 12 节磁盘满    → 资源问题，先腾空间再重建。"
