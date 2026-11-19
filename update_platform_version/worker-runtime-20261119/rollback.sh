#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
B="${1:?必须指定 apply 输出的备份目录}"
[[ "$(docker inspect --format '{{.Id}}' "$C")" == "$(cat "$B/backend-id")" ]]
[[ "$(docker inspect --format '{{.Id}}' "$F")" == "$(cat "$B/frontend-id")" ]]
docker cp "$B/frontend.conf" "$F:/etc/nginx/conf.d/default.conf"
docker exec "$F" nginx -t
docker exec "$F" nginx -s reload
for f in settings.py asgi.py; do docker cp "$B/wharttest_django/$f" "$C:/app/wharttest_django/$f"; done
docker cp "$B/supervisord.rollback-drain.conf" "$C:/app/supervisord.conf"
docker restart -t 180 "$C"
wait_backend
echo '已恢复旧路由、单 Web 与单 Celery 消费者；新增队列仍可消费。'
