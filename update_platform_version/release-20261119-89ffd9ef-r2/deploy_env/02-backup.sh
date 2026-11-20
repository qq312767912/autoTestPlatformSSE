#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
[[ -f "$HERE/.images-validated" ]] && [[ "$(cat "$HERE/.images-validated")" == "$REV" ]] || { echo "请先完成03-import-images.sh目标机验证"; exit 1; }
bash "$HERE/00-preflight.sh"
rm -f "$HERE/.latest-backup"
B="$HERE/../backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$B"
python3 "$HERE/snapshot_runtime.py" "$B"
python3 "$HERE/prepare_assets.py" "$B" "$HERE"
python3 "$HERE/prepare_runtime.py" "$B" "$HERE"
project=$(cat "$B/project")
docker compose -p "$project" -f "$B/compose.runtime.json" config -q
docker compose -p "$project" -f "$B/compose.rollback.json" config -q
stopped=()
restore_on_failure() { if [[ ${#stopped[@]} -gt 0 ]]; then docker start "${stopped[@]}" >/dev/null; fi; }
trap restore_on_failure EXIT
for c in wharttest-actuator-{01,02,03} wharttest-frontend wharttest-mcp wharttest-playwright-mcp wharttest-backend wharttest-crg; do
 echo "[停写] $c"
 if [[ "$(docker inspect --format '{{.State.Running}}' "$c")" == true ]]; then docker stop -t 240 "$c" >/dev/null; stopped+=("$c"); fi
done
printf '%s\n' "${stopped[@]}" > "$B/stopped-containers"
echo "[备份] PostgreSQL逻辑备份"
docker exec wharttest-postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -1 > "$B/postgres.sql.gz"
gzip -t "$B/postgres.sql.gz"
data=$(docker inspect wharttest-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')
[[ -d "$data" ]] || { echo '找不到持久化数据目录';exit 1; }
echo "[备份] 应用数据：$data（保留全部数据，可能需要较长时间）"
tar -C "$data" -czf "$B/app-data.tar.gz" .
gzip -t "$B/app-data.tar.gz"
printf '%s\n' "$data" > "$B/data-source"
touch "$B/BACKUP_COMPLETE"
printf '%s\n' "$B" > "$HERE/.latest-backup"
trap - EXIT
echo "备份完成：$B。应用保持停止，请立即执行04-deploy.sh；取消升级则执行06-rollback-app.sh。"
