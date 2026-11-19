#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C=wharttest-backend
F=wharttest-frontend
wait_backend() {
 local state
 for ((i=0;i<90;i++)); do
  state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$C")
  if [[ "$state" == healthy ]]; then return 0; fi
  echo "等待后端启动 $((i+1))/90：$state"
  sleep 10
 done
 docker logs --tail 80 "$C"
 return 1
}
