#!/usr/bin/env bash
set -euo pipefail
B="${1:?指定apply输出的备份目录}"
C=wharttest-backend
[[ "$(docker inspect --format '{{.Id}}' "$C")" == "$(cat "$B/container-id")" ]] || { echo '容器已替换，停止回滚';exit 1; }
docker cp "$B/ones_import.py" "$C:/app/knowledge/ones_import.py"
docker restart -t 240 "$C"
for ((i=0;i<90;i++)); do
 if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$C")" == healthy ]]; then echo '回滚完成，后端健康';exit 0;fi
 sleep 10
done
echo '旧文件已恢复，但后端健康检查未通过';exit 1
