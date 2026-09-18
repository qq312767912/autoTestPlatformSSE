#!/usr/bin/env bash
set -uo pipefail

TARGET_HOST="${1:-www.test.sse.com.cn}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
PLAYWRIGHT_CONTAINER="${PLAYWRIGHT_CONTAINER:-wharttest-playwright-mcp}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/internal-dns-$(date '+%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1

section() {
  echo
  echo "========== $* =========="
}

run_check() {
  local title="$1"
  shift
  echo
  echo "--- $title ---"
  "$@" 2>&1 || echo "[WARN] 检查失败，退出码: $?"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

echo "WHartTest 内网 DNS 诊断"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "目标域名: $TARGET_HOST"
echo "Backend 容器: $BACKEND_CONTAINER"
echo "Playwright MCP 容器: $PLAYWRIGHT_CONTAINER"
echo "日志文件: $LOG_FILE"

section "宿主机"
run_check "系统信息" uname -a
run_check "DNS 配置" sh -c 'cat /etc/resolv.conf'
if command -v getent >/dev/null 2>&1; then
  run_check "getent 解析" getent hosts "$TARGET_HOST"
elif command -v nslookup >/dev/null 2>&1; then
  run_check "nslookup 解析" nslookup "$TARGET_HOST"
else
  run_check "ping 解析" ping -c 1 -W 2 "$TARGET_HOST"
fi

section "Docker 概况"
run_check "Docker 版本" docker version
run_check "WHartTest 容器" docker ps -a --filter name=wharttest --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'

for container in "$BACKEND_CONTAINER" "$PLAYWRIGHT_CONTAINER"; do
  section "容器: $container"
  if ! container_exists "$container"; then
    echo "[ERROR] 容器不存在: $container"
    continue
  fi

  run_check "状态与镜像" docker inspect "$container" --format \
    'name={{.Name}} image={{.Config.Image}} status={{.State.Status}} running={{.State.Running}}'
  run_check "DNS/Hosts 容器配置" docker inspect "$container" --format \
    'dns={{json .HostConfig.Dns}} dnsSearch={{json .HostConfig.DnsSearch}} extraHosts={{json .HostConfig.ExtraHosts}} networkMode={{.HostConfig.NetworkMode}}'
  run_check "Docker 网络地址" docker inspect "$container" --format \
    '{{range $name, $cfg := .NetworkSettings.Networks}}network={{$name}} ip={{$cfg.IPAddress}} gateway={{$cfg.Gateway}}{{println}}{{end}}'
  run_check "/etc/resolv.conf" docker exec "$container" sh -c 'cat /etc/resolv.conf'
  run_check "/etc/hosts" docker exec "$container" sh -c 'cat /etc/hosts'
  run_check "容器内可用命令" docker exec "$container" sh -c \
    'for cmd in getent nslookup ping curl wget python python3 node; do command -v "$cmd" 2>/dev/null || true; done'

  echo
  echo "--- 容器内域名解析 ---"
  docker exec -e DIAG_HOST="$TARGET_HOST" "$container" sh -c '
    if command -v getent >/dev/null 2>&1; then
      getent hosts "$DIAG_HOST"
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c "import os,socket; print(socket.getaddrinfo(os.environ[\"DIAG_HOST\"], 80))"
    elif command -v python >/dev/null 2>&1; then
      python -c "import os,socket; print(socket.getaddrinfo(os.environ[\"DIAG_HOST\"], 80))"
    elif command -v node >/dev/null 2>&1; then
      node -e "require(\"dns\").lookup(process.env.DIAG_HOST,{all:true},console.log)"
    else
      echo "[WARN] 容器内没有可用的 DNS 诊断命令"
      exit 2
    fi
  ' 2>&1 || echo "[FAIL] $container 无法解析 $TARGET_HOST"

  echo
  echo "--- 容器内 HTTP 连通性 ---"
  docker exec -e DIAG_HOST="$TARGET_HOST" "$container" sh -c '
    url="http://$DIAG_HOST"
    if command -v curl >/dev/null 2>&1; then
      curl -I -L --connect-timeout 5 --max-time 15 "$url"
    elif command -v wget >/dev/null 2>&1; then
      wget -S --spider -T 15 "$url"
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c "import os,urllib.request; print(urllib.request.urlopen(\"http://\"+os.environ[\"DIAG_HOST\"], timeout=15).status)"
    elif command -v python >/dev/null 2>&1; then
      python -c "import os,urllib.request; print(urllib.request.urlopen(\"http://\"+os.environ[\"DIAG_HOST\"], timeout=15).status)"
    else
      echo "[WARN] 容器内没有可用的 HTTP 诊断命令"
      exit 2
    fi
  ' 2>&1 || echo "[FAIL] $container 无法访问 http://$TARGET_HOST"
done

section "Docker 网络详情"
for network in $(docker inspect "$BACKEND_CONTAINER" "$PLAYWRIGHT_CONTAINER" \
  --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{println}}{{end}}' 2>/dev/null | sort -u); do
  run_check "网络 $network" docker network inspect "$network" --format \
    'name={{.Name}} driver={{.Driver}} internal={{.Internal}} ipam={{json .IPAM.Config}} containers={{range .Containers}}{{.Name}}={{.IPv4Address}} {{end}}'
done

section "结束"
echo "诊断完成。请将以下日志文件发回："
echo "$LOG_FILE"
