#!/usr/bin/env bash
# ============================================================================
# 24-sync-bundled-skills.sh —— 把内置技能补齐到宿主机「外置技能目录」
# ============================================================================
# 背景：基础 compose 里有一条 ./skills:/app/bundled_skills:ro，即容器里的
#   /app/bundled_skills 实际来自宿主机 offline-images/skills，而不是镜像本体。
#   因此「把技能烤进镜像」对正常部署不生效 —— 技能修复必须落到这个宿主机目录。
#   05-verify.sh 里对技能文件的断言校验的也正是这个目录。
#
# 用法：
#   bash 24-sync-bundled-skills.sh            # 只诊断，不改任何文件（默认）
#   bash 24-sync-bundled-skills.sh --apply    # 只补缺失文件，已存在的不同内容不动
#   bash 24-sync-bundled-skills.sh --apply --force   # 内容不同的也覆盖（先自动备份）
#
# 正常升级【不需要手工执行本脚本】：04-deploy.sh 会在替换容器之前自动调用它
# （--apply --force），并在 Backend 健康后自动执行 init_skills 刷新数据库快照。
# 保留本脚本作为：① 只读诊断当前差异；② 04 之外单独补技能/回滚后的补救手段。
#
# 安全约定：
#   * 默认只读；任何写动作都要显式 --apply。
#   * 写入前先把目标技能目录整体打包备份到 deploy_env/backups/。
#   * 默认不覆盖已存在文件，避免顶掉你在内网手工调整过的技能。
#   * 改完宿主机目录即时对容器生效（只读 bind mount），无需重启容器。
set -euo pipefail

APPLY=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[错误] 未知参数：$arg（支持 --apply / --force / --help）" >&2; exit 2 ;;
  esac
done

BASE_DIR="${BASE_DIR:-/projects/ai-test-platform}"
UPDATE_DIR="${UPDATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
HOTFIX_SKILLS="$UPDATE_DIR/hotfix/bundled_skills"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKUP_DIR="$UPDATE_DIR/backups"

# ------------------------------------------------------------ 目标目录推导
# 基础 compose 里是相对路径挂载 `./skills:/app/bundled_skills:ro`，
# 相对的是 **compose 文件所在目录**。所以从本次实际使用的 BASE_COMPOSE 推导，
# 比硬编码 /projects/ai-test-platform 更可靠（改过安装根目录也不会错）。
BASE_COMPOSE="${BASE_COMPOSE:-$BASE_DIR/offline-images/docker-compose.offline.yml}"
SKILLS_DIR_EXPLICIT=0
if [ -n "${SKILLS_DIR:-}" ]; then
  SKILLS_DIR_EXPLICIT=1
elif [ -f "$BASE_COMPOSE" ]; then
  SKILLS_DIR="$(cd "$(dirname "$BASE_COMPOSE")" && pwd)/skills"
else
  SKILLS_DIR="$BASE_DIR/offline-images/skills"
  echo "[警告] 找不到基础 compose：$BASE_COMPOSE" >&2
  echo "       已回退到默认路径推导：$SKILLS_DIR；如与实际部署不符请用 SKILLS_DIR=... 显式指定。" >&2
fi

# ---------------------------------------------------------------- 选源
# 优先用 deploy_env 自带的 hotfix/bundled_skills（与镜像内容一致，内网本地就有），
# 避免为了补一个技能还要 docker 跑镜像。
if [ -d "$HOTFIX_SKILLS" ] && [ -n "$(find "$HOTFIX_SKILLS" -type f -print -quit 2>/dev/null)" ]; then
  SOURCE_DIR="$HOTFIX_SKILLS"
  SOURCE_DESC="deploy_env/hotfix/bundled_skills（与镜像内副本同内容）"
elif docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  SOURCE_DIR="$(mktemp -d)"
  trap 'rm -rf "$SOURCE_DIR"' EXIT
  docker cp "$BACKEND_CONTAINER:/app/bundled_skills/." "$SOURCE_DIR/" >/dev/null
  SOURCE_DESC="容器 $BACKEND_CONTAINER:/app/bundled_skills（直接取自运行中的镜像）"
else
  echo "[错误] 找不到技能源：既没有 $HOTFIX_SKILLS，也没有可用的容器 $BACKEND_CONTAINER" >&2
  exit 1
fi

echo "=========================================="
echo "技能源  ：$SOURCE_DIR"
echo "        （$SOURCE_DESC）"
echo "目标目录：$SKILLS_DIR"
echo "模式    ：$([ "$APPLY" = 1 ] && echo "同步（$([ "$FORCE" = 1 ] && echo '含覆盖' || echo '仅补缺失')）" || echo '只诊断，不写入')"
echo "=========================================="
echo

# ------------------------------------------------- 与运行中容器交叉校验（只读）
# 以容器「实际挂载源」为准做一次比对：路径推断一旦与实际部署不符，
# 最坏情况是把技能写到另一个自建目录、而容器根本看不到，故这里必须提示。
_norm_dir() { # 归一化目录（解析软链、去掉尾斜杠）；不是目录时原样返回
  if [ -d "$1" ]; then (cd "$1" && pwd -P); else printf '%s' "$1"; fi
}

if docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  live_src="$(docker inspect "$BACKEND_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/app/bundled_skills"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
  if [ -z "$live_src" ]; then
    echo "[警告] 容器 $BACKEND_CONTAINER 上未见 /app/bundled_skills 挂载。"
    echo "       此时容器内技能来自镜像内副本，同步宿主机目录不会生效。" >&2
  elif [ "$(_norm_dir "$live_src")" != "$(_norm_dir "$SKILLS_DIR")" ]; then
    echo "[错误] 实际挂载源与推算的目标目录不一致，写下去容器看不到任何改动：" >&2
    echo "       容器实际挂载源：$live_src" >&2
    echo "       本脚本将写入  ：$SKILLS_DIR" >&2
    if [ "$SKILLS_DIR_EXPLICIT" = 1 ]; then
      echo "       （SKILLS_DIR 由你显式指定，故仅告警不中断；确认无误可忽略本提示）" >&2
    else
      echo "       处理：确认 BASE_COMPOSE 指向的是本次实际使用的 compose 文件（当前：$BASE_COMPOSE），" >&2
      echo "             或显式指定：SKILLS_DIR=$live_src bash $(basename "${BASH_SOURCE[0]}") ..." >&2
      echo "       已中止，未写入任何文件。" >&2
      exit 1
    fi
  else
    echo "[校验] 目标目录与 $BACKEND_CONTAINER 的实际挂载源一致（$live_src）。"
  fi
  echo
fi

if [ ! -d "$SKILLS_DIR" ]; then
  echo "[提示] 目标目录尚不存在，将由本脚本创建：$SKILLS_DIR"
  missing_dir=1
else
  missing_dir=0
fi

# ------------------------------------------------- 逐文件对比（只读）
missing=0
differ=0
same=0
missing_list=""
differ_list=""

while IFS= read -r src; do
  rel="${src#"$SOURCE_DIR"/}"
  dst="$SKILLS_DIR/$rel"
  if [ ! -e "$dst" ]; then
    missing=$((missing + 1)); missing_list="${missing_list}${rel}"$'\n'
  elif ! cmp -s "$src" "$dst"; then
    differ=$((differ + 1)); differ_list="${differ_list}${rel}"$'\n'
  else
    same=$((same + 1))
  fi
done < <(find "$SOURCE_DIR" -type f | sort)

echo "--- 文件对比 ---"
printf '  缺失（目标没有）      ：%d\n' "$missing"
[ -n "$missing_list" ] && printf '%s' "$missing_list" | sed 's/^/      - /'
printf '  内容不同（目标已有）  ：%d\n' "$differ"
[ -n "$differ_list" ] && printf '%s' "$differ_list" | sed 's/^/      ~ /'
printf '  完全一致              ：%d\n' "$same"
echo

if [ "$APPLY" != 1 ]; then
  echo "[只诊断] 未改动任何文件。确认上面清单后再执行：bash $(basename "${BASH_SOURCE[0]}") --apply"
  # 用 if 而不是 `[ ... ] && echo`：后者在条件为假时让整个列表返回 1，
  # 配合 set -e 会让「纯诊断且一切一致」这种最正常的情况以退出码 1 结束。
  if [ "$differ" -gt 0 ]; then
    echo "         注意有 $differ 个文件内容不同，默认不会被覆盖；确需覆盖请加 --force。"
  fi
  exit 0
fi

# ------------------------------------------------------------- 写动作
to_copy="$missing"
[ "$FORCE" = 1 ] && to_copy=$((missing + differ))

if [ "$to_copy" -eq 0 ]; then
  echo "[完成] 无需写入：技能目录已是最新（缺失 0，且$([ "$FORCE" = 1 ] && echo '内容全部一致' || echo '未启用覆盖')）。"
  if [ "${SKILLS_SYNC_BY_DEPLOY:-0}" = 1 ]; then
    echo "[提醒] 数据库技能快照由 04-deploy.sh 在 Backend 健康后自动刷新，此处无需手工操作。"
  else
    echo "[提醒] 目录已到位，但数据库里的技能快照未必是最新的；如刚补过文件，请刷新："
    echo "       docker exec $BACKEND_CONTAINER /opt/venv/bin/python /app/manage.py init_skills"
  fi
  exit 0
fi

if [ "$missing_dir" = 0 ]; then
  mkdir -p "$BACKUP_DIR"
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup="$BACKUP_DIR/skills-backup-$stamp.tar.gz"
  tar czf "$backup" -C "$SKILLS_DIR" . 2>/dev/null
  echo "[备份] $backup（$(du -h "$backup" | awk '{print $1}')）"
else
  echo "[备份] 跳过（目标目录原不存在，无内容可备份）"
fi

copied=0
while IFS= read -r src; do
  rel="${src#"$SOURCE_DIR"/}"
  dst="$SKILLS_DIR/$rel"
  if [ -e "$dst" ] && [ "$FORCE" != 1 ]; then
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp -p "$src" "$dst"
  copied=$((copied + 1))
  echo "  [写入] $rel"
done < <(find "$SOURCE_DIR" -type f | sort)

chmod -R a+rX "$SKILLS_DIR" 2>/dev/null || true
echo
echo "[完成] 已写入 $copied 个文件。"

# -------------------------------------------------- 改写后复查 + 容器可见性
echo
echo "--- 复查 ---"
recheck_bad=0
while IFS= read -r src; do
  rel="${src#"$SOURCE_DIR"/}"
  dst="$SKILLS_DIR/$rel"
  cmp -s "$src" "$dst" || { echo "  [异常] 仍不一致：$rel" >&2; recheck_bad=$((recheck_bad + 1)); }
done < <(find "$SOURCE_DIR" -type f | sort)
[ "$recheck_bad" -eq 0 ] && echo "  [通过] 源与目标逐文件一致"

if docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  if docker exec "$BACKEND_CONTAINER" test -f /app/bundled_skills/test-case-clarity-review/references/review-rules.md; then
    echo "  [通过] 容器内 /app/bundled_skills/test-case-clarity-review/references/review-rules.md 已可见（只读挂载即时生效）"
  else
    echo "  [失败] 容器内仍看不到该文件，请检查挂载：docker inspect $BACKEND_CONTAINER --format '{{range .Mounts}}{{println .Source \" -> \" .Destination}}{{end}}'" >&2
    exit 1
  fi
else
  echo "  [跳过] 容器 $BACKEND_CONTAINER 不在运行，容器内可见性未校验"
fi
echo
echo "=========================================="
if [ "${SKILLS_SYNC_BY_DEPLOY:-0}" = 1 ]; then
  # 由 04-deploy.sh 自动调用：刷库与后续校验由它接管，避免提示里出现"多余的第二步"。
  echo "[下一步] 本脚本由 04-deploy.sh 自动调用：Backend 健康后它会执行 init_skills 刷新数据库快照，"
  echo "         并在最后提示你运行 05-verify.sh，此处无需手工操作。"
else
  echo "[下一步] 文件层面已同步，但审查真正读到的技能内容来自数据库："
  echo "         docker exec $BACKEND_CONTAINER /opt/venv/bin/python /app/manage.py init_skills"
  echo "         只补宿主机目录不刷数据库，界面与审查结果仍是旧版。"
  echo "[下一步] 然后重跑校验：bash $UPDATE_DIR/05-verify.sh"
  echo "         （05-verify.sh 的技能断言为三层校验：文件是否存在、review-rules.md 是否含第 9 条"
  echo "           「严重程度参考」、SKILL.md 是否含「完成条件」章节；跑绿即代表宿主机目录里是完整版）"
fi
echo "=========================================="
