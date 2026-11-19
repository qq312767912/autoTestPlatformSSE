#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
B=$(cat "$HERE/.latest-backup")
[[ -f "$B/BACKUP_COMPLETE" ]]
[[ "$(docker inspect --format '{{.State.Running}}' wharttest-backend)" == false ]] || { echo "后端仍在运行，请先执行02-backup.sh完成停写备份"; exit 1; }
project=$(cat "$B/project")
compose=(docker compose -p "$project" -f "$B/compose.runtime.json")
trap 'echo "部署失败，开始恢复原容器配置与镜像"; bash "$HERE/06-rollback-app.sh" "$B"' ERR
"${compose[@]}" up -d --no-deps --pull never semgrep-scanner crg-service
wait_healthy wharttest-semgrep
wait_healthy wharttest-crg
"${compose[@]}" up -d --no-deps --pull never backend
wait_healthy wharttest-backend
docker exec wharttest-backend python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8001/api/auth/login-key/",timeout=15).read()'
"${compose[@]}" up -d --no-deps --pull never frontend actuator-01 actuator-02 actuator-03
for c in wharttest-frontend wharttest-actuator-{01,02,03}; do wait_healthy "$c"; done
for c in wharttest-mcp wharttest-playwright-mcp; do
 if grep -qx "$c" "$B/stopped-containers"; then docker start "$c" >/dev/null; fi
done
bash "$HERE/05-verify.sh"
trap - ERR
printf '%s\n' "$B" > "$HERE/.installed-backup"
echo '升级成功，原端口映射、网络与业务数据挂载已保留。请进行登录、多人聊天、审查及UI执行器业务验收。'
