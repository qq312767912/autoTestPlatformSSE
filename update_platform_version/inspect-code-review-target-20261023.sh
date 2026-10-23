#!/usr/bin/env bash
set -euo pipefail

BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
OUTPUT_DIR="${OUTPUT_DIR:-.}"
mkdir -p "$OUTPUT_DIR"
LOG_FILE="$OUTPUT_DIR/code-review-target-$(date +%Y%m%d-%H%M%S).log"

command -v docker >/dev/null || { echo "缺少 docker" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "缺少 sha256sum" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 不可访问" >&2; exit 1; }

{
  echo "代码审查内网目标只读检查"
  echo "时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "宿主机架构: $(uname -m)"
  for container in "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER"; do
    echo
    echo "=== 容器 $container ==="
    if ! docker inspect "$container" >/dev/null 2>&1; then
      echo "未找到容器"
      continue
    fi
    docker inspect "$container" --format '状态={{.State.Status}} 镜像名={{.Config.Image}} 镜像ID={{.Image}}'
    docker inspect "$container" --format '{{range .Mounts}}{{if eq .Destination "/app/code_analysis"}}代码目录挂载={{.Source}}{{end}}{{if eq .Destination "/usr/share/nginx/html"}}前端目录挂载={{.Source}}{{end}}{{end}}'
  done

  echo
  echo "=== Backend 运行时 ==="
  if docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
    docker exec "$BACKEND_CONTAINER" sh -c 'uname -m; sed -n "1,3p" /etc/os-release; /opt/venv/bin/python --version; ldd --version 2>&1 | head -2' || true
    for file in services.py tasks.py views.py models.py review_mcp.py; do
      echo "--- /app/code_analysis/$file ---"
      docker exec "$BACKEND_CONTAINER" sh -c "if [ -f /app/code_analysis/$file ]; then sha256sum /app/code_analysis/$file; else echo missing; fi" || true
    done
    docker exec "$BACKEND_CONTAINER" sh -c 'grep -n "CODE_REVIEW_REPORT_SCHEMA_VERSION\|def retry_verification_analysis\|def retry_verification" /app/code_analysis/services.py /app/code_analysis/views.py 2>/dev/null | head -15' || true
    echo "=== 当前代码审查活动任务数（不输出任务内容） ==="
    docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py shell -c 'from code_analysis.models import AnalysisTask; print(AnalysisTask.objects.filter(status__in=["fetching","machine_analyzing","ai_analyzing","generating_tests","queued"]).count())' 2>&1 | tail -8 || true
  fi

  echo
  echo "=== Frontend 静态文件 ==="
  if docker inspect "$FRONTEND_CONTAINER" >/dev/null 2>&1; then
    docker exec "$FRONTEND_CONTAINER" sh -c 'if [ -f /usr/share/nginx/html/index.html ]; then sha256sum /usr/share/nginx/html/index.html; else echo "index.html missing"; fi; find /usr/share/nginx/html/assets -maxdepth 1 -type f 2>/dev/null | wc -l' || true
  fi
  echo
  echo "检查结束；脚本未修改容器、镜像或数据库。"
} | tee "$LOG_FILE"

echo "请回传日志: $LOG_FILE"
