#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
umask 077
if [[ -f "$BASE/SHA256SUMS" ]]; then (cd "$BASE" && sha256sum -c SHA256SUMS); fi
command -v timeout >/dev/null
[[ "$(docker inspect --format '{{.Config.Image}}' "$C")" == 'wharttest-250-backend:update-20261109-ai-coverage-r1-arm64' ]] || { echo '镜像与采集版本不符'; exit 1; }
# 先在目标运行时检查控制响应及运行任务，不清理任何队列。
timeout 40 docker exec -w /app "$C" python -c 'from wharttest_django.celery import app; r=app.control.inspect(timeout=15).active(); assert r and not any(r.values()), "消费者忙碌或无响应，请等待任务结束"'
B="$BASE/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$B/wharttest_django"
docker cp "$C:/app/supervisord.conf" "$B/supervisord.conf"
for f in settings.py asgi.py; do docker cp "$C:/app/wharttest_django/$f" "$B/wharttest_django/$f"; done
docker cp "$F:/etc/nginx/conf.d/default.conf" "$B/frontend.conf"
docker inspect --format '{{.Id}}' "$C" > "$B/backend-id"
docker inspect --format '{{.Id}}' "$F" > "$B/frontend-id"
# 保存独立回滚配置：单消费者承接新增队列，不篡改原始备份。
sed 's/-Q celery,task_center/-Q celery,task_center,code_analysis,testcase_review,knowledge,ui_execution/' "$B/supervisord.conf" > "$B/supervisord.rollback-drain.conf"
if docker exec "$C" test -f /app/wharttest_django/web_runtime.py; then
 echo '目标已存在 web_runtime.py，停止以保护已有改动'; exit 1
fi
STAGE="/tmp/wharttest-runtime-$(date +%Y%m%d%H%M%S)-$$"
docker exec "$C" mkdir -p "$STAGE/candidate/wharttest_django"
for f in patch_runtime.py supervisor.sha256 core-program.conf core-location.conf web_runtime.py; do docker cp "$BASE/$f" "$C:$STAGE/$f"; done
docker cp "$B/." "$C:$STAGE/candidate/"
docker exec "$C" python "$STAGE/patch_runtime.py" "$STAGE/candidate"
echo "备份：$B"
trap 'echo "安装失败，执行自动回滚"; bash "$BASE/rollback.sh" "$B"' ERR
# 候选全部生成成功后才写运行路径。
for f in supervisord.conf wharttest_django/settings.py wharttest_django/asgi.py; do docker exec "$C" cp "$STAGE/candidate/$f" "/app/$f"; done
docker exec "$C" cp "$STAGE/web_runtime.py" /app/wharttest_django/web_runtime.py
docker exec -w /app "$C" python -c 'import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE","wharttest_django.settings"); import django; django.setup(); from wharttest_django.asgi import application; from wharttest_django.celery import app; app.loader.import_default_modules(); app.autodiscover_tasks(force=True); assert "testcases.review_tasks.execute_testcase_review" in app.tasks'
docker restart -t 180 "$C"
wait_backend
# 后端新端口可用后，才切换前端；任何失败均恢复两侧配置。
docker exec "$C" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8001/api/auth/login-key/",timeout=15).read()'
docker cp "$C:$STAGE/candidate/frontend.conf" "$B/frontend.candidate.conf"
docker cp "$B/frontend.candidate.conf" "$F:/etc/nginx/conf.d/default.conf"
docker exec "$F" nginx -t
docker exec "$F" nginx -s reload
bash "$BASE/check.sh"
trap - ERR
echo "安装通过；回滚命令：sudo bash $BASE/rollback.sh $B"
