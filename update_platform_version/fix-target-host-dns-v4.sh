#!/usr/bin/env bash
# 为 WHartTest 内网增加「目标系统测试域名」映射（默认 star.test.sseinfo.com -> 10.122.214.188）。
#
# 背景：Docker 为每个容器单独生成 /etc/hosts，不继承宿主机 /etc/hosts，
# 所以宿主机上配了 hosts，容器内照样 ERR_NAME_NOT_RESOLVED。
#
# 本项目既有的做法就是 compose extra_hosts（见 docker-compose.update.yml 里
# www.sse.com.cn 等 5 个域名 -> 10.122.215.111）。本脚本沿用同一机制，并且
# 「写进正式 compose」而不是临时覆盖层，原因：
#   * 临时覆盖层（fix-actuator-internal-dns.sh 的写法）会让容器的配置哈希和正式
#     compose 不一致，下一次 04-deploy.sh 的 up -d 会判定配置漂移而多做一次重建；
#     对锚点里本来就有的域名无害，但对新增域名会直接把映射丢掉。
#   * 写进正式 compose 后哈希始终一致，映射长期有效。
#
# compose 模式（默认）：改 compose + 只重建受影响服务，重启/重建后仍有效。
# exec 模式：只改容器内 /etc/hosts，立即生效、不重启任何容器（容器重建后失效）。
#
# 用法：
# V4 会在正式执行时清理目标服务的半重建/名称冲突容器，再逐个重建。
# Backend 重建后会自动重启 Frontend Nginx，使其重新解析 Backend 容器 IP，避免 502。
# 只删除/重建 Backend、Playwright MCP、三个 Actuator 和可选 Vision MCP；
# Frontend 仅重启，不重建，不动任何数据卷及其他服务。
#
#   bash fix-target-host-dns-v4.sh                      # 预演：校验并打印将要做的改动
#   bash fix-target-host-dns-v4.sh --with-vision --yes  # 一次性修复冲突、重建、刷新前端代理并验证
set -euo pipefail

PLATFORM_DIR="${PLATFORM_DIR:-/projects/ai-test-platform}"
BASE_COMPOSE="${BASE_COMPOSE:-${PLATFORM_DIR}/offline-images/docker-compose.offline.yml}"
UPDATE_COMPOSE="${UPDATE_COMPOSE:-${PLATFORM_DIR}/update_platform_version/deploy_env/docker-compose.update.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-offline-images}"

# 插入的每一行都带这个标记，--clean 只删自己写的东西。
ENTRY_MARK='WHARTTEST-TARGET-HOST'

TARGET_HOSTS="${TARGET_HOSTS:-star.test.sseinfo.com:10.122.214.188}"
MODE="${MODE:-compose}"
WITH_VISION=0
DO_CLEAN=0
DO_HTTP=1
ASSUME_YES=0
FORCE_REPLACE=0
SERVICES_OVERRIDE=""
RECREATE_WAIT_SECONDS="${RECREATE_WAIT_SECONDS:-120}"

# Actuator 容器通过 Compose secret 读取平台账号密码。内网升级包可能没有
#预先生成该文件；只在文件缺失/为空时使用默认值，绝不覆盖现有密钥。
ACTUATOR_SECRET_FILE="${ACTUATOR_SECRET_FILE:-${PLATFORM_DIR}/update_platform_version/deploy_env/secrets/actuator_api_password}"
DEFAULT_ACTUATOR_API_PASSWORD="${ACTUATOR_DEFAULT_PASSWORD:-}"

# compose 模式原始文件的备份路径（exec 模式也会在收尾信息里引用，必须先定义）
BAK_COMPOSE="${UPDATE_COMPOSE}.wharttest-target-host.bak"

# exec 模式沿用平台同步器的受管标记，将来平台「测试域名配置」上线后可接管同一块内容。
BEGIN_MARKER='# BEGIN WHARTTEST MANAGED HOSTS'
END_MARKER='# END WHARTTEST MANAGED HOSTS'

# ------------------------------------------------------------------ 输出
LOG_FILE="${LOG_FILE:-${PLATFORM_DIR}/update_platform_version/fix-target-host-dns-$(date +%Y%m%d-%H%M%S).log}"
die() { echo "[失败] $*" >&2; exit 1; }
ok() { echo "[通过] $*"; }
info() { echo "[信息] $*"; }
warn() { echo "[警告] $*" >&2; }
step() { echo; echo "--- $* ---"; }

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

可选参数（也可用同名环境变量）：
  --hosts <域名:IPv4>[,<域名:IPv4>...]  要写入的映射（默认 star.test.sseinfo.com:10.122.214.188）
  --mode compose|exec                   写入方式（默认 compose，持久）
  --with-vision                         同时给 vision-mcp 加映射（默认不加）
  --services "<svc...>"                 自定义要重建的服务名（默认自动推导）
  --yes, -y                             确认写盘；不加则等同于预演
  --clean                               撤销本脚本写入的内容
  --force-replace                       目标域名已存在但 IP 不同时替换，而不是中止
  --no-http                             跳过 HTTP 连通性验证
  -h, --help                            显示本帮助
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hosts) [ "$#" -ge 2 ] || die "--hosts 缺少取值"; TARGET_HOSTS="$2"; shift 2 ;;
    --mode) [ "$#" -ge 2 ] || die "--mode 缺少取值"; MODE="$2"; shift 2 ;;
    --services) [ "$#" -ge 2 ] || die "--services 缺少取值"; SERVICES_OVERRIDE="$2"; shift 2 ;;
    --with-vision) WITH_VISION=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --clean) DO_CLEAN=1; shift ;;
    --force-replace) FORCE_REPLACE=1; shift ;;
    --no-http) DO_HTTP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1" ;;
  esac
done

case "$MODE" in compose|exec) ;; *) die "--mode 只能是 compose / exec，收到：$MODE" ;; esac

# ------------------------------------------------------------------ 取值校验
is_ipv4() {
  local ip="$1" octet
  [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  local IFS=.
  for octet in $ip; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
    [ "$octet" -le 255 ] || return 1
  done
  return 0
}

is_hostname() {
  local h="$1"
  [ -n "$h" ] && [ "${#h}" -le 253 ] || return 1
  case "$h" in *..*|.*|*.) return 1 ;; esac
  [[ "$h" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]
}

HOSTNAMES=()
IPS=()
IFS=',' read -r -a _pairs <<< "$TARGET_HOSTS"
[ "${#_pairs[@]}" -gt 0 ] || die "--hosts 为空"
for pair in "${_pairs[@]}"; do
  [ -n "$pair" ] || die "--hosts 中存在空项"
  hostname="${pair%%:*}"
  ip="${pair#*:}"
  [ "$hostname" != "$pair" ] || die "映射缺少 IPv4：$pair（格式应为 域名:IPv4）"
  is_hostname "$hostname" || die "非法域名：$hostname"
  is_ipv4 "$ip" || die "非法 IPv4：$ip（来自 $pair）"
  HOSTNAMES+=("$hostname")
  IPS+=("$ip")
done

ENTRIES=""
for i in "${!HOSTNAMES[@]}"; do
  ENTRIES="${ENTRIES}${ENTRIES:+,}${HOSTNAMES[$i]}:${IPS[$i]}"
done

# 只确保日志目录存在，不修改已存在目录（如 /tmp）的权限。
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

echo "=============================================================="
echo "WHartTest 目标系统测试域名映射"
echo "时间     : $(date '+%Y-%m-%d %H:%M:%S')"
echo "映射     : ${TARGET_HOSTS}"
echo "模式     : ${MODE}$( [ "$DO_CLEAN" -eq 1 ] && echo ' (撤销)' )$( [ "$ASSUME_YES" -eq 0 ] && echo ' [预演]' )"
echo "compose  : ${UPDATE_COMPOSE}"
echo "日志     : ${LOG_FILE}"
echo "=============================================================="

# ------------------------------------------------------------------ 容器内脚本（exec 模式）
# 仅用 POSIX sh + grep/awk/mktemp（busybox 与 coreutils 都有）。
# 两个要点：
#   1) 受管标记成对校验放在改写之前：标记残缺时直接中止，绝不把 /etc/hosts 截断。
#   2) 改写后裁掉末尾空行，再补「一个空行 + 受管块」，使重复执行逐字节一致。
INJECT_SCRIPT='set -eu
hosts=/etc/hosts
bc=$(grep -Fxc "# BEGIN WHARTTEST MANAGED HOSTS" "$hosts" || true)
ec=$(grep -Fxc "# END WHARTTEST MANAGED HOSTS" "$hosts" || true)
if [ "$bc" != "$ec" ]; then
  echo "受管标记不完整(begin=$bc,end=$ec)，已中止以免截断 $hosts" >&2
  exit 1
fi
if [ "$bc" -gt 1 ]; then
  echo "受管标记重复($bc 组)，已中止以免误删 $hosts" >&2
  exit 1
fi
tmp=$(mktemp)
trap "rm -f \"$tmp\"" EXIT
awk '"'"'
  $0 == "# BEGIN WHARTTEST MANAGED HOSTS" { managed=1; next }
  $0 == "# END WHARTTEST MANAGED HOSTS" { managed=0; next }
  managed { next }
  { n++; line[n]=$0 }
  END { last=0; for (i=1;i<=n;i++) if (line[i] != "") last=i
        for (i=1;i<=last;i++) print line[i] }
'"'"' "$hosts" > "$tmp"
printf "\n" >> "$tmp"
cat >> "$tmp"
cat "$tmp" > "$hosts"
exit 0'

CLEAN_SCRIPT='set -eu
hosts=/etc/hosts
bc=$(grep -Fxc "# BEGIN WHARTTEST MANAGED HOSTS" "$hosts" || true)
ec=$(grep -Fxc "# END WHARTTEST MANAGED HOSTS" "$hosts" || true)
if [ "$bc" != "$ec" ]; then
  echo "受管标记不完整(begin=$bc,end=$ec)，已中止以免截断 $hosts" >&2
  exit 1
fi
if [ "$bc" -eq 0 ]; then
  exit 0
fi
tmp=$(mktemp)
trap "rm -f \"$tmp\"" EXIT
awk '"'"'
  $0 == "# BEGIN WHARTTEST MANAGED HOSTS" { managed=1; next }
  $0 == "# END WHARTTEST MANAGED HOSTS" { managed=0; next }
  managed { next }
  { n++; line[n]=$0 }
  END { last=0; for (i=1;i<=n;i++) if (line[i] != "") last=i
        for (i=1;i<=last;i++) print line[i] }
'"'"' "$hosts" > "$tmp"
cat "$tmp" > "$hosts"
exit 0'

HTTP_SCRIPT='u="http://$1/"
if command -v curl >/dev/null 2>&1; then
  code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$u" 2>/dev/null || true)
  if [ -n "$code" ] && [ "$code" != "000" ]; then echo "HTTP $code"; exit 0; fi
  echo "curl 未取得响应"; exit 1
fi
if command -v wget >/dev/null 2>&1; then
  if wget -q -T 10 -O /dev/null "$u" >/dev/null 2>&1; then
    echo "HTTP 2xx/3xx"; exit 0
  fi
  echo "wget 未成功（可能为 4xx/5xx，域名解析已通）"; exit 0
fi
echo "SKIP 容器内无 curl/wget，跳过 HTTP 验证"; exit 0'

# ------------------------------------------------------------------ exec 模式的受管块
SORTED=()
for i in "${!HOSTNAMES[@]}"; do SORTED+=("${HOSTNAMES[$i]}|${IPS[$i]}"); done
IFS=$'\n' SORTED=($(printf '%s\n' "${SORTED[@]}" | LC_ALL=C sort))
unset IFS

render_block() {
  printf '%s\n' "$BEGIN_MARKER"
  local line
  for line in "${SORTED[@]}"; do printf '%s\t%s\n' "${line#*|}" "${line%%|*}"; done
  printf '%s\n' "$END_MARKER"
}
PAYLOAD="$(render_block)"

# ------------------------------------------------------------------ 前置校验
command -v docker >/dev/null 2>&1 || die "未找到 docker 命令"

if [ "$MODE" = "compose" ]; then
  docker compose version >/dev/null 2>&1 || die "docker compose 不可用"
  [ -f "$BASE_COMPOSE" ] || die "基础 Compose 不存在：$BASE_COMPOSE"
  [ -f "$UPDATE_COMPOSE" ] || die "升级 Compose 不存在：$UPDATE_COMPOSE"
else
  docker info >/dev/null 2>&1 || die "docker 不可用（请确认当前用户可执行 docker）"
fi

# 执行器默认走 compose 锚点：三个执行器共用 x-actuator-common，改一处即全覆盖。
# 老版本若没有锚点，退化为逐个服务写。
ACTUATOR_TARGETS=()
if grep -qE '^[A-Za-z0-9_-]+:[[:space:]]*&actuator-common' "$UPDATE_COMPOSE" 2>/dev/null \
   && grep -qE '^[[:space:]]*<<:[[:space:]]*\*actuator-common' "$UPDATE_COMPOSE" 2>/dev/null; then
  ACTUATOR_TARGETS=(x-actuator-common)
  ACTUATOR_NOTE="锚点 x-actuator-common（覆盖 actuator-01/02/03）"
else
  ACTUATOR_TARGETS=(actuator-01 actuator-02 actuator-03)
  ACTUATOR_NOTE="逐个服务（未发现 x-actuator-common 锚点）"
fi

COMPOSE_TARGETS=("${ACTUATOR_TARGETS[@]}" backend playwright-mcp)
[ "$WITH_VISION" -eq 1 ] && COMPOSE_TARGETS+=(vision-mcp)

if [ -n "$SERVICES_OVERRIDE" ]; then
  read -r -a SERVICES <<< "$SERVICES_OVERRIDE"
else
  SERVICES=(backend playwright-mcp actuator-01 actuator-02 actuator-03)
  [ "$WITH_VISION" -eq 1 ] && SERVICES+=(vision-mcp)
fi

container_of() { echo "wharttest-$1"; }
CONTAINERS=()
for svc in "${SERVICES[@]}"; do CONTAINERS+=("$(container_of "$svc")"); done

echo "目标服务 : ${COMPOSE_TARGETS[*]}"
echo "执行器   : ${ACTUATOR_NOTE}"
echo "将重建   : ${SERVICES[*]}"

ensure_actuator_secret() {
  local svc needs_secret=0
  for svc in "${SERVICES[@]}"; do
    case "$svc" in actuator-01|actuator-02|actuator-03) needs_secret=1 ;; esac
  done
  [ "$needs_secret" -eq 1 ] || return 0

  if [ -s "$ACTUATOR_SECRET_FILE" ]; then
    ok "Actuator 密钥文件已存在（不覆盖）"
    return 0
  fi

  [ -n "$DEFAULT_ACTUATOR_API_PASSWORD" ] || die "Actuator 密钥文件不存在，请设置 ACTUATOR_DEFAULT_PASSWORD"

  mkdir -p "$(dirname "$ACTUATOR_SECRET_FILE")"
  chmod 700 "$(dirname "$ACTUATOR_SECRET_FILE")"
  umask 077
  printf '%s' "$DEFAULT_ACTUATOR_API_PASSWORD" > "$ACTUATOR_SECRET_FILE"
  chmod 600 "$ACTUATOR_SECRET_FILE"
  ok "Actuator 密钥文件缺失，已根据 ACTUATOR_DEFAULT_PASSWORD 创建（密码不输出）"
}

# ------------------------------------------------------------------ compose 补丁器
# 纪律：先在内存里算出结果，结构断言/compose 解析任一不过就中止，原文件一个字节都不动。
run_patcher() {
  local src="$1" dst="$2" clean="$3"
  awk -v TARGETS="${COMPOSE_TARGETS[*]}" \
      -v ENTRIES="$ENTRIES" \
      -v MARK="$ENTRY_MARK" \
      -v CLEAN="$clean" \
      -v FORCE="$FORCE_REPLACE" '
  function add_line(at, text,   x) { x = nIns[at] + 1; nIns[at] = x; ins[at, x] = text }
  function indent_of(s,   n) { n = 0; while (substr(s, n + 1, 1) == " ") n++; return n }
  function spaces(n,   s) { s = ""; while (length(s) < n) s = s " "; return s }
  function item_line(ind, h, i) { return sprintf("%s- \"%s:%s\"  # %s", spaces(ind), h, i, MARK) }
  function item_part(s, want_ip,   t, p) {
    t = s
    sub(/^[ \t]*-[ \t]*/, "", t)
    # 先去掉行尾注释（本脚本自己写入的标记），否则重复执行会把自己的标记当成 IP 的一部分，
    # 从而把"已经写好的"误判成"冲突"。
    sub(/[ \t]+#.*$/, "", t)
    sub(/^"/, "", t); sub(/"$/, "", t)
    p = index(t, ":")
    if (p == 0) return ""
    return want_ip ? substr(t, p + 1) : substr(t, 1, p - 1)
  }
  { ln++; L[ln] = $0 }
  END {
    nt = split(TARGETS, T, " ")
    for (i = 1; i <= nt; i++) tgt[i] = T[i]
    ne = split(ENTRIES, E, ",")
    for (i = 1; i <= ne; i++) {
      p = index(E[i], ":")
      ehost[i] = substr(E[i], 1, p - 1)
      eip[i] = substr(E[i], p + 1)
    }

    # ---- 归属标注：每行属于哪个服务 / 哪个顶层块
    curTop = ""; curSvc = ""
    for (i = 1; i <= ln; i++) {
      line = L[i]
      if (line ~ /^[ \t]*$/) { svcOf[i] = curSvc; continue }
      ind = indent_of(line)
      body = substr(line, ind + 1)
      if (ind == 0) {
        if (body ~ /^services:[ \t]*$/) { curTop = "services"; curSvc = "" }
        else {
          curTop = body
          if (body ~ /^[A-Za-z0-9_.-]+:[ \t]*&/) {
            svc = body; sub(/:.*$/, "", svc); curSvc = svc
          } else curSvc = ""
        }
      } else if (curTop == "services" && ind == 2 && body ~ /^[A-Za-z0-9_.-]+:[ \t]*$/) {
        svc = body; sub(/:.*$/, "", svc); curSvc = svc
      }
      svcOf[i] = curSvc
    }

    # ---- 定位每个目标的 extra_hosts 头
    for (k = 1; k <= nt; k++) { hdr[k] = 0; ehInd[k] = 0; insAt[k] = 0; created[k] = 0 }
    for (i = 1; i <= ln; i++) {
      for (k = 1; k <= nt; k++) {
        if (svcOf[i] != tgt[k] || hdr[k] != 0) continue
        if (L[i] ~ /^[ \t]*$/) continue
        ind = indent_of(L[i]); body = substr(L[i], ind + 1)
        if (body ~ /^extra_hosts:[ \t]*$/) { hdr[k] = i; ehInd[k] = ind }
      }
    }

    # ---- 收集块内条目并定位块尾
    for (k = 1; k <= nt; k++) {
      lastItem[k] = hdr[k]; nItem[k] = 0
      if (hdr[k] == 0) continue
      for (i = hdr[k] + 1; i <= ln; i++) {
        if (svcOf[i] != tgt[k]) break
        line = L[i]
        if (line ~ /^[ \t]*$/) continue
        ind = indent_of(line)
        if (ind <= ehInd[k]) break
        body = substr(line, ind + 1)
        if (body ~ /^-/) {
          nItem[k]++
          iIdx[k, nItem[k]] = i
          iHost[k, nItem[k]] = item_part(body, 0)
          iIp[k, nItem[k]] = item_part(body, 1)
          lastItem[k] = i
        }
      }
    }

    # ---- 逐目标逐域名：不存在则追加；已存在同 IP 则跳过；已存在异 IP 则冲突
    nConflict = 0
    for (k = 1; k <= nt; k++) {
      for (e = 1; e <= ne; e++) {
        found = 0
        for (m = 1; m <= nItem[k]; m++) if (iHost[k, m] == ehost[e]) { found = m; break }
        if (found) {
          if (iIp[k, found] == eip[e]) continue
          if (FORCE == 1) {
            at = iIdx[k, found]
            repl[at] = item_line(indent_of(L[at]), ehost[e], eip[e])
            continue
          }
          nConflict++
          conflict[nConflict] = tgt[k] " 中 " ehost[e] " 已映射到 " iIp[k, found] "，与期望 " eip[e] " 不一致"
          continue
        }
        if (hdr[k] == 0) {
          if (insAt[k] == 0) {
            for (i = 1; i <= ln; i++) if (svcOf[i] == tgt[k] && L[i] ~ /^[ \t]*image:/) insAt[k] = i
            if (insAt[k] == 0) for (i = 1; i <= ln; i++) if (svcOf[i] == tgt[k]) { insAt[k] = i; break }
          }
          if (insAt[k] == 0) { nConflict++; conflict[nConflict] = "找不到服务 " tgt[k] " 的插入位置"; continue }
          # 缩进必须由文件自身推导：services 下的键在 4 空格，锚点的键在 2 空格。
          ki = indent_of(L[insAt[k]])
          if (created[k] == 0) {
            add_line(insAt[k], sprintf("%sextra_hosts:", spaces(ki)))
            created[k] = 1
          }
          add_line(insAt[k], item_line(ki + 2, ehost[e], eip[e]))
        } else {
          add_line(lastItem[k], item_line(ehInd[k] + 2, ehost[e], eip[e]))
        }
      }
    }

    if (CLEAN == 1) {
      # ---- 撤销：删带标记的行；因标记行清空而变空的 extra_hosts 头也必须删掉
      # （空 extra_hosts: 会被 compose 判为 "must be a mapping" 而解析失败）
      o = 0
      for (i = 1; i <= ln; i++) {
        if (index(L[i], MARK) > 0) continue
        o++; O[o] = L[i]
      }
      for (i = 1; i <= o; i++) {
        if (O[i] !~ /^[ \t]*extra_hosts:[ \t]*$/) continue
        hind = indent_of(O[i]); empty = 1
        for (j = i + 1; j <= o; j++) {
          if (O[j] ~ /^[ \t]*$/) continue
          if (indent_of(O[j]) > hind) empty = 0
          break
        }
        if (empty) drop[i] = 1
      }
      # 空白行必须保留：只丢弃「因此变空的 extra_hosts 头」，逐行删空行会把整个文件的
      # 排版打散，撤销就不再是"还原"。
      for (i = 1; i <= o; i++) if (!(i in drop)) print O[i]
      exit 0
    }

    if (nConflict > 0) {
      print "CONFLICT" > "/dev/stderr"
      for (c = 1; c <= nConflict; c++) print conflict[c] > "/dev/stderr"
      exit 3
    }

    for (i = 1; i <= ln; i++) {
      print (i in repl ? repl[i] : L[i])
      if (i in nIns) for (j = 1; j <= nIns[i]; j++) print ins[i, j]
    }
  }
  ' "$src" > "$dst"
}

# ------------------------------------------------------------------ 只信任「compose 解析出来的结果」
resolved_hosts_of() {
  local svc="$1"
  awk -v svc="$svc" '
    $0 ~ "^  " svc ":$" { insvc = 1; next }
    insvc && /^  [A-Za-z0-9_-]+:[ \t]*$/ { insvc = 0 }
    insvc && /^    [A-Za-z0-9_-]+:/ { key = $1; ink = 1; next }
    insvc && ink && /^      - / {
      if (key == "extra_hosts:") { v = $0; sub(/^ *- /, "", v); print v }
      next
    }
  ' "$RESOLVED_FILE"
}

# ------------------------------------------------------------------ exec 模式
precheck_exec() {
  local c
  for c in "${CONTAINERS[@]}"; do
    state="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || true)"
    [ -n "$state" ] || die "容器 $c 不存在"
    [ "$state" = running ] || die "容器 $c 状态为 $state，请先启动"
    docker exec "$c" /bin/sh -c 'test -w /etc/hosts' || die "容器 $c 的 /etc/hosts 不可写"
    docker exec "$c" /bin/sh -c 'command -v awk >/dev/null 2>&1 && command -v mktemp >/dev/null 2>&1' \
      || die "容器 $c 内缺少 awk/mktemp，无法安全改写 /etc/hosts"
    docker exec "$c" /bin/sh -c '
      b=$(grep -Fxc "# BEGIN WHARTTEST MANAGED HOSTS" /etc/hosts || true)
      e=$(grep -Fxc "# END WHARTTEST MANAGED HOSTS" /etc/hosts || true)
      [ "$b" = "$e" ] && [ "${b:-0}" -le 1 ]
    ' || die "容器 $c 的 /etc/hosts 中受管标记残缺或重复，已中止以免截断该文件"
  done
  ok "前置校验通过（容器都在运行、/etc/hosts 可写、awk/mktemp 可用、受管标记成对）"
}

inject_exec_all() {
  local c rc=0
  for c in "${CONTAINERS[@]}"; do
    if [ "$DO_CLEAN" -eq 1 ]; then
      if docker exec -i -u 0 "$c" /bin/sh -c "$CLEAN_SCRIPT" < /dev/null; then
        ok "$c : 已清除受管块"
      else
        warn "$c : 清除失败"; rc=1
      fi
    else
      if printf '%s\n' "$PAYLOAD" | docker exec -i -u 0 "$c" /bin/sh -c "$INJECT_SCRIPT"; then
        ok "$c : 已写入受管块"
      else
        warn "$c : 写入失败"; rc=1
      fi
    fi
  done
  return $rc
}

wait_ready() {
  local container="$1" limit="$2" elapsed=0 health=""
  while [ "$elapsed" -lt "$limit" ]; do
    health="$(docker inspect "$container" \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
    case "$health" in
      healthy|running) echo "[通过] $container: $health"; return 0 ;;
      unhealthy|exited|dead)
        docker logs --tail 80 "$container" 2>&1 || true
        return 1 ;;
    esac
    sleep 3
    elapsed=$((elapsed + 3))
  done
  warn "$container 在 ${limit}s 内未就绪（当前 ${health:-未知}）"
  return 1
}

remove_conflicting_target_containers() {
  local svc c id ids=""

  step "清理目标服务的半重建/名称冲突容器"
  for svc in "${SERVICES[@]}"; do
    # Compose 重建中断时，旧容器可能被改名为 <id>_wharttest-xxx；
    # 通过 project+service 标签精确收集，不使用模糊 name filter。
    while IFS= read -r id; do
      [ -n "$id" ] && ids="${ids}${ids:+$'\n'}${id}"
    done < <(docker ps -aq \
      --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${svc}")

    # 同时精确检查约定容器名，兼容标签丢失的异常容器。
    c="$(container_of "$svc")"
    id="$(docker inspect "$c" --format '{{.Id}}' 2>/dev/null || true)"
    [ -z "$id" ] || ids="${ids}${ids:+$'\n'}${id}"
  done

  if [ -z "$ids" ]; then
    info "未发现遗留容器"
    return 0
  fi

  ids="$(printf '%s\n' "$ids" | awk 'NF && !seen[$0]++')"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    docker inspect "$id" --format '[清理] {{.Name}}  {{.State.Status}}  {{.Config.Image}}' 2>/dev/null || true
  done <<< "$ids"

  while IFS= read -r id; do
    [ -n "$id" ] || continue
    docker rm -f "$id" >/dev/null || die "无法删除冲突容器：$id"
  done <<< "$ids"
  ok "目标服务的遗留容器已清理（未删除任何数据卷）"
}

recreate_services_one_by_one() {
  local svc rc=0
  for svc in "$@"; do
    step "逐个重建：$svc"
    "${compose[@]}" up -d --pull never --no-deps --force-recreate "$svc" || return 1
    wait_ready "$(container_of "$svc")" "$RECREATE_WAIT_SECONDS" || rc=1
    [ "$rc" -eq 0 ] || return 1
  done
}

refresh_frontend_proxy() {
  local frontend_container="wharttest-frontend"

  step "刷新 Frontend Nginx 的 Backend 解析（修复 502）"
  if docker inspect "$frontend_container" >/dev/null 2>&1; then
    docker restart "$frontend_container" >/dev/null \
      || die "Frontend 重启失败：$frontend_container"
  else
    warn "$frontend_container 不存在，将通过 Compose 创建"
    "${compose[@]}" up -d --pull never --no-deps frontend \
      || die "Frontend 创建失败"
  fi
  wait_ready "$frontend_container" "$RECREATE_WAIT_SECONDS" \
    || die "Frontend 未就绪，详见日志：$LOG_FILE"
  ok "Frontend Nginx 已重新解析 Backend"
}

# ================================================================== 主流程
if [ "$MODE" = "exec" ]; then
  step "exec 模式：写入容器内 /etc/hosts（不重启）"
  if [ "$ASSUME_YES" -eq 0 ]; then
    echo "将要写入的受管块："
    printf '%s\n' "$PAYLOAD"
    info "预演结束，未修改任何容器。确认执行：bash $0 --mode exec --yes"
    exit 0
  fi
  precheck_exec
  inject_exec_all || die "存在失败项，详见日志：$LOG_FILE"
else
  step "compose 模式：写入正式 compose 的 extra_hosts"

  # 在改动 compose 之前准备好重建 Actuator 必需的 bind secret，避免配置
  # 已落盘、容器却因缺少挂载源而重建失败的半完成状态。预演不写入密钥。
  if [ "$ASSUME_YES" -eq 1 ]; then
    ensure_actuator_secret
  elif [ ! -s "$ACTUATOR_SECRET_FILE" ]; then
    warn "Actuator 密钥文件缺失；正式执行时将自动创建：$ACTUATOR_SECRET_FILE"
  fi

  NEW_COMPOSE="$(mktemp)"
  trap 'rm -f "${NEW_COMPOSE:-}"' EXIT

  set +e
  run_patcher "$UPDATE_COMPOSE" "$NEW_COMPOSE" "$DO_CLEAN" 2>"$NEW_COMPOSE.err"
  patch_rc=$?
  set -e
  if [ "$patch_rc" -eq 3 ]; then
    cat "$NEW_COMPOSE.err" >&2
    die "未做任何改动。确认要改就加 --force-replace；否则请先手工核对 compose。"
  elif [ "$patch_rc" -ne 0 ]; then
    cat "$NEW_COMPOSE.err" >&2 || true
    die "改写 compose 失败（退出码 $patch_rc），未做任何改动"
  fi
  ok "补丁生成完成（尚未落盘）"

  # 自己的结构性断言：改了就要有可数的痕迹（撤销时方向相反，必须"一点不剩"）
  new_marks="$(grep -c "$ENTRY_MARK" "$NEW_COMPOSE" || true)"
  if [ "$DO_CLEAN" -eq 1 ]; then
    [ "$new_marks" -eq 0 ] || die "撤销后仍残留 ${new_marks} 行标记，已中止"
  else
    [ "$new_marks" -ge "${#HOSTNAMES[@]}" ] || die "仅写入 ${new_marks} 行，少于期望的 ${#HOSTNAMES[@]} 个映射，已中止"
    for h in "${HOSTNAMES[@]}"; do
      [ "$(grep -c -F "$h" "$NEW_COMPOSE" || true)" -ge 1 ] || die "新 compose 中找不到 $h，已中止"
    done
  fi
  ok "结构断言通过（标记 ${new_marks} 行）"

  # 真正的判据：让 compose 解析新文件，看解析结果里有没有这个映射
  compose=(docker compose -p "$COMPOSE_PROJECT" -f "$BASE_COMPOSE" -f "$NEW_COMPOSE")
  "${compose[@]}" config > "$NEW_COMPOSE.resolved" 2>"$NEW_COMPOSE.err" \
    || { cat "$NEW_COMPOSE.err" >&2; die "compose 无法解析改写后的文件，已中止（原文件未动）"; }
  ok "compose 解析通过"

  RESOLVED_FILE="$NEW_COMPOSE.resolved"
  for svc in "${SERVICES[@]}"; do
    for i in "${!HOSTNAMES[@]}"; do
      if resolved_hosts_of "$svc" | grep -qxF "${HOSTNAMES[$i]}=${IPS[$i]}"; then
        if [ "$DO_CLEAN" -eq 1 ]; then
          die "撤销后 $svc 仍解析出 ${HOSTNAMES[$i]}=${IPS[$i]}，已中止（原文件未动）"
        fi
        ok "解析结果 $svc : ${HOSTNAMES[$i]}=${IPS[$i]}"
      else
        if [ "$DO_CLEAN" -eq 1 ]; then
          ok "解析结果 $svc : 已不含 ${HOSTNAMES[$i]}"
          continue
        fi
        echo "[失败] 解析结果中 $svc 缺少 ${HOSTNAMES[$i]}=${IPS[$i]}，已中止" >&2
        echo "该服务当前解析到的 extra_hosts：" >&2
        resolved_hosts_of "$svc" | sed 's/^/    /' >&2
        exit 1
      fi
    done
  done

  step "改动预览"
  diff -u "$UPDATE_COMPOSE" "$NEW_COMPOSE" | sed -n '1,40p' || true

  if [ "$ASSUME_YES" -eq 0 ]; then
    echo
    info "预演结束（原文件未改动）。确认执行：bash $0 --yes"
    exit 0
  fi

  # V4 的正式执行是一次性恢复流程：先清理上次 Compose recreate
  # 中断留下的冲突实例。--clean 仅撤销配置，不执行这个恢复步骤。
  if [ "$DO_CLEAN" -eq 0 ]; then
    remove_conflicting_target_containers
  fi

  # 状态已一致时不做无谓重建：重建 backend/执行器会带来 1~2 分钟抖动。
  if cmp -s "$UPDATE_COMPOSE" "$NEW_COMPOSE"; then
    # 上一次可能已写入 compose，但在重建中途因 secret/挂载缺失而失败。
    # 因此不能只比较文件，还要核对运行容器的 HostConfig.ExtraHosts。
    DRIFT_SERVICES=()
    for svc in "${SERVICES[@]}"; do
      c="$(container_of "$svc")"
      drift=0
      [ "$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || true)" = running ] || drift=1
      for i in "${!HOSTNAMES[@]}"; do
        docker inspect "$c" --format '{{range .HostConfig.ExtraHosts}}{{println .}}{{end}}' 2>/dev/null \
          | grep -qxF "${HOSTNAMES[$i]}:${IPS[$i]}" || drift=1
      done
      [ "$drift" -eq 0 ] || DRIFT_SERVICES+=("$svc")
    done

    if [ "${#DRIFT_SERVICES[@]}" -eq 0 ]; then
      ok "compose 与所有容器都已是目标状态，跳过重建"
    else
      step "compose 已写入，但容器配置未生效：重建漂移服务"
      info "服务：${DRIFT_SERVICES[*]}"
      recreate_services_one_by_one "${DRIFT_SERVICES[@]}" \
        || die "有服务未就绪，详见日志：$LOG_FILE"
    fi
  else
    step "写入 compose"
    if [ ! -f "$BAK_COMPOSE" ]; then
      cp -a "$UPDATE_COMPOSE" "$BAK_COMPOSE"
      info "原始文件已备份：$BAK_COMPOSE"
    fi
    cat "$NEW_COMPOSE" > "${UPDATE_COMPOSE}.tmp-patch"
    mv "${UPDATE_COMPOSE}.tmp-patch" "$UPDATE_COMPOSE"
    ok "已写入 $UPDATE_COMPOSE"

    compose=(docker compose -p "$COMPOSE_PROJECT" -f "$BASE_COMPOSE" -f "$UPDATE_COMPOSE")
    "${compose[@]}" config >/dev/null || die "落盘后 compose 解析失败，请从备份恢复：$BAK_COMPOSE"

    step "重建受影响服务（--no-deps，不动数据库/Redis/Qdrant/frontend）"
    info "服务：${SERVICES[*]}"
    recreate_services_one_by_one "${SERVICES[@]}" \
      || die "有服务未就绪，详见日志：$LOG_FILE"
  fi
fi

# Nginx 对静态 proxy_pass 中的 Docker 服务名通常在启动/重载时解析。
# Backend 容器被删除重建后 IP 可能改变，必须刷新 Frontend，否则 API 会 502。
if [ "$MODE" = compose ] && [ "$DO_CLEAN" -eq 0 ]; then
  refresh_frontend_proxy
fi

# ------------------------------------------------------------------ 验证
step "验证：容器内实际解析结果"
VERIFY_FAILED=0
for c in "${CONTAINERS[@]}"; do
  state="$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || true)"
  if [ -z "$state" ]; then
    warn "$c 容器不存在，无法验证"
    if [ "$DO_CLEAN" -eq 0 ]; then VERIFY_FAILED=1; fi
    continue
  fi
  for i in "${!HOSTNAMES[@]}"; do
    resolved="$(docker exec "$c" getent hosts "${HOSTNAMES[$i]}" 2>/dev/null | awk 'NR==1{print $1}' || true)"
    if [ "$DO_CLEAN" -eq 1 ]; then
      if [ "$resolved" = "${IPS[$i]}" ]; then
        warn "$c : ${HOSTNAMES[$i]} 仍解析为 $resolved（可能来自其它机制，请人工核对）"
      else
        ok "$c : ${HOSTNAMES[$i]} 已不再映射到 ${IPS[$i]}"
      fi
    elif [ "$resolved" = "${IPS[$i]}" ]; then
      ok "$c : ${HOSTNAMES[$i]} -> $resolved"
    else
      warn "$c : ${HOSTNAMES[$i]} 解析为 '${resolved:-无}'，期望 ${IPS[$i]}"
      VERIFY_FAILED=1
    fi
  done
done

# 目标站点可能要求登录而返回 4xx/5xx，故只作参考，不参与判定。
if [ "$DO_HTTP" -eq 1 ] && [ "$DO_CLEAN" -eq 0 ]; then
  step "HTTP 连通性（参考项，不参与判定）"
  for c in "${CONTAINERS[@]}"; do
    [ -n "$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || true)" ] || continue
    for hostname in "${HOSTNAMES[@]}"; do
      result="$(docker exec "$c" /bin/sh -c "$HTTP_SCRIPT" sh "$hostname" 2>/dev/null || echo '无法验证')"
      echo "  $c : http://$hostname/ -> $result"
    done
  done
fi

echo
if [ "$VERIFY_FAILED" -ne 0 ]; then
  die "存在未通过的验证项，详见日志：$LOG_FILE"
fi
if [ "$DO_CLEAN" -eq 1 ]; then
  if [ -f "$BAK_COMPOSE" ]; then
    echo "已撤销。改动前的 compose 备份保留在：$BAK_COMPOSE"
  else
    echo "已撤销（exec 模式未改动 compose，无需备份）。"
  fi
else
  echo "完成。请在平台重新执行用例。"
  echo "该映射已在正式 compose 中，后续 04-deploy.sh / 05-verify.sh 的 up -d 不会撤掉它。"
fi
echo "日志文件: $LOG_FILE"
