#!/usr/bin/env bash
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REV=20261119-89ffd9ef-r2
IMAGE_DIR="${IMAGES_DIR:-$HERE/../images}"
wait_healthy() {
 local c="$1" s
 for ((i=0;i<90;i++)); do
  s=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c")
  if [[ "$s" == healthy ]]; then return 0; fi
  echo "等待 $c：$s ($((i+1))/90)"
  sleep 10
 done
 docker logs --tail 80 "$c";return 1
}
