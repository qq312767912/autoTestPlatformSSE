#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
B="${1:-$(python3 "$HERE/resolve_backup.py" "$HERE")}"
[[ -f "$B/BACKUP_COMPLETE" ]]
python3 "$HERE/prepare_assets.py" "$B" "$HERE"
python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);from prepare_runtime import protect_mcp;protect_mcp(sys.argv[1])' "$HERE"
project=$(cat "$B/project")
docker compose -p "$project" -f "$B/compose.rollback.json" up -d --no-deps --pull never
while IFS= read -r c; do [[ -z "$c" ]] || docker start "$c" >/dev/null; done < "$B/stopped-containers"
wait_healthy wharttest-backend
wait_healthy wharttest-frontend
echo "已恢复部署前容器镜像与挂载。数据库迁移未自动撤销；数据库及文件完整备份在 $B，若需数据回退须单独核对后恢复。"
