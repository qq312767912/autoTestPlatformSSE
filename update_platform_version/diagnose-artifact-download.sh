#!/usr/bin/env bash
set -u

BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-wharttest-frontend}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:8913}"
REPORT="artifact-download-$(date +%Y%m%d-%H%M%S).log"

exec > >(tee "$REPORT") 2>&1

section() {
  printf '\n========== %s ==========\n' "$1"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

section "基本信息"
date '+时间: %F %T %z'
printf '主机: '; hostname
printf '架构: '; uname -m
printf 'Docker: '; docker version --format '{{.Server.Version}}' 2>/dev/null || true
printf '前端访问地址: %s\n' "$FRONTEND_URL"

section "容器状态"
docker ps -a --filter "name=^/${BACKEND_CONTAINER}$" --filter "name=^/${FRONTEND_CONTAINER}$" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

for container in "$BACKEND_CONTAINER" "$FRONTEND_CONTAINER"; do
  section "${container} 挂载"
  if container_exists "$container"; then
    docker inspect "$container" --format '{{range .Mounts}}{{println .Type "|" .Source "->" .Destination "| RW=" .RW}}{{end}}'
  else
    printf 'ERROR: 容器不存在: %s\n' "$container"
  fi
done

section "后端媒体目录"
if container_exists "$BACKEND_CONTAINER"; then
  docker exec "$BACKEND_CONTAINER" sh -lc '
    printf "MEDIA_ROOT相关环境变量:\n"
    env | grep "^MEDIA_ROOT=" || true
    printf "\n/app/data/media权限:\n"
    ls -ld /app/data /app/data/media /app/data/media/skill_runtime 2>&1 || true
    printf "\n最近生成的xlsx文件（最多30个）:\n"
    find /app/data/media/skill_runtime -type f -name "*.xlsx" -printf "%TY-%Tm-%Td %TH:%TM:%TS | %s bytes | %p\n" 2>/dev/null | sort -r | head -30
  '
fi

section "前端媒体目录"
if container_exists "$FRONTEND_CONTAINER"; then
  docker exec "$FRONTEND_CONTAINER" sh -lc '
    printf "/app/data/media权限:\n"
    ls -ld /app/data /app/data/media /app/data/media/skill_runtime 2>&1 || true
    printf "\n前端可见的xlsx文件（最多30个）:\n"
    find /app/data/media/skill_runtime -type f -name "*.xlsx" -printf "%TY-%Tm-%Td %TH:%TM:%TS | %s bytes | %p\n" 2>/dev/null | sort -r | head -30
  '
fi

section "Nginx媒体配置"
if container_exists "$FRONTEND_CONTAINER"; then
  docker exec "$FRONTEND_CONTAINER" sh -lc '
    nginx -T 2>&1 | grep -n -A8 -B2 "location.*media" || true
  '
fi

section "文件可见性对比"
BACKEND_LIST="$(mktemp)"
FRONTEND_LIST="$(mktemp)"
trap 'rm -f "$BACKEND_LIST" "$FRONTEND_LIST"' EXIT

if container_exists "$BACKEND_CONTAINER"; then
  docker exec "$BACKEND_CONTAINER" sh -lc \
    'find /app/data/media/skill_runtime -type f -name "*.xlsx" -printf "%P\n" 2>/dev/null | sort' > "$BACKEND_LIST"
fi
if container_exists "$FRONTEND_CONTAINER"; then
  docker exec "$FRONTEND_CONTAINER" sh -lc \
    'find /app/data/media/skill_runtime -type f -name "*.xlsx" -printf "%P\n" 2>/dev/null | sort' > "$FRONTEND_LIST"
fi

printf '后端文件数: %s\n' "$(wc -l < "$BACKEND_LIST" | tr -d ' ')"
printf '前端文件数: %s\n' "$(wc -l < "$FRONTEND_LIST" | tr -d ' ')"
printf '\n仅后端可见的文件（最多30个）:\n'
comm -23 "$BACKEND_LIST" "$FRONTEND_LIST" | head -30

section "下载地址测试"
if [ ! -s "$BACKEND_LIST" ]; then
  printf 'WARN: 后端未找到 skill_runtime 下的 xlsx 文件。\n'
else
  tail -10 "$BACKEND_LIST" | while IFS= read -r relative_path; do
    encoded_path="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe="/"))' "$relative_path" 2>/dev/null || printf '%s' "$relative_path")"
    url="${FRONTEND_URL%/}/media/skill_runtime/${encoded_path}"
    printf '\nURL: %s\n' "$url"
    curl --connect-timeout 5 --max-time 20 -sS -o /dev/null \
      -w 'HTTP=%{http_code} Content-Type=%{content_type} Size=%{size_download} Redirect=%{redirect_url}\n' \
      "$url" || true
  done
fi

section "结论提示"
if [ -s "$BACKEND_LIST" ] && ! cmp -s "$BACKEND_LIST" "$FRONTEND_LIST"; then
  printf 'DIAGNOSIS: 后端与前端看到的文件不一致，优先检查两个容器的宿主机数据卷是否指向同一个 media 目录。\n'
elif [ -s "$BACKEND_LIST" ]; then
  printf 'INFO: 前后端文件列表一致；请根据上方HTTP状态检查Nginx路由、权限或文件名编码。\n'
else
  printf 'DIAGNOSIS: 后端媒体目录中没有生成文件，需要检查技能输出目录和后端日志。\n'
fi

printf '\n日志文件: %s/%s\n' "$(pwd)" "$REPORT"
