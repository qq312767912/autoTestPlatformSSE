#!/usr/bin/env bash
# ============================================================================
# 24-sync-bundled-skills.sh —— 把内置技能（全量版）补齐到宿主机「外置技能目录」
# ============================================================================
# 背景：
#   * 基础 compose 有一条 ./skills:/app/bundled_skills:ro，容器里的 /app/bundled_skills
#     实际来自宿主机 offline-images/skills，而不是镜像本体 —— 所以「把技能烤进镜像」
#     对正常部署不生效，技能必须落到这个宿主机目录。
#   * apply 同步的是**全量版**技能（SKILL.md 5815 B + references/review-rules.md 4247 B
#     + agents/openai.yaml）；镜像里那份是早期精简兜底版，且被上面的挂载盖住、
#     对内网不生效。05-verify.sh 里的技能断言校验的也正是宿主机这个目录。
#   * 顺带把商店用的 zip 放进外置技能目录，便于在平台 UI「上传 Skill」手动安装。
#
# 用法：
#   bash 24-sync-bundled-skills.sh                   # 只诊断，不改任何文件（默认）
#   bash 24-sync-bundled-skills.sh --apply           # 只补缺失文件；已存在的不同内容不动
#   bash 24-sync-bundled-skills.sh --apply --force   # 内容不同的也覆盖（先自动备份）
#                                                    # → 旧版/精简版升级为全量版走这条
#   bash 24-sync-bundled-skills.sh --apply --no-manifest   # 跳过 manifest.json 条目合并
#   bash 24-sync-bundled-skills.sh --apply --no-db-sync    # 跳过容器内 init_skills 刷新
#
# ⚠️ 为什么还要刷数据库：审查时真正送进模型的是**数据库里 Skill 的 skill_content**
#   （review_service.build_skill_snapshot 优先用 DB 内容 + media 目录下的 references）。
#   宿主机目录只是「技能来源」，DB 内容由容器启动时的 init_skills 从该目录同步。
#   所以改完挂载目录后若不刷新，界面/审查用的仍是旧的精简版。本脚本默认用
#   docker exec 跑一次 init_skills（等价于重启 Backend 做的事，但不中断服务）。
#
# 安全约定：
#   * 默认只读；任何写动作都要显式 --apply。
#   * 写入前先把目标技能目录整体打包备份到 deploy_env/backups/。
#   * 默认不覆盖已存在文件，避免顶掉你在内网手工调整过的技能。
#   * 改完宿主机目录即时对容器生效（只读 bind mount）；数据库侧由本脚本的 init_skills
#     步骤刷新 —— 两者都无需重启容器。
set -euo pipefail

APPLY=0
FORCE=0
MANIFEST=1
DB_SYNC=1
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --force|--upgrade) FORCE=1 ;;
    --no-manifest) MANIFEST=0 ;;
    --no-db-sync) DB_SYNC=0 ;;
    -h|--help) sed -n '2,33p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[错误] 未知参数：$arg（支持 --apply / --force / --no-manifest / --no-db-sync / --help）" >&2; exit 2 ;;
  esac
done

BASE_DIR="${BASE_DIR:-/projects/ai-test-platform}"
SKILLS_DIR="${SKILLS_DIR:-$BASE_DIR/offline-images/skills}"
UPDATE_DIR="${UPDATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
HOTFIX_SKILLS="$UPDATE_DIR/hotfix/bundled_skills"
ZIP_SRC="$UPDATE_DIR/skills/test-case-clarity-review.zip"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-wharttest-backend}"
BACKUP_DIR="$UPDATE_DIR/backups"
SKILL_NAME="test-case-clarity-review"
# 全量版特征串：精简兜底版没有这两段，用它区分，避免把精简版当成「已同步完成」。
RULES_SIGNATURE="## 9. 严重程度参考"
SKILL_SIGNATURE="## 完成条件"

# ---------------------------------------------------------------- 选源
# 优先用 deploy_env 自带的 hotfix/bundled_skills（内网本地就有，不必 docker 跑镜像）。
if [ -d "$HOTFIX_SKILLS" ] && [ -n "$(find "$HOTFIX_SKILLS" -type f -print -quit 2>/dev/null)" ]; then
  SOURCE_DIR="$HOTFIX_SKILLS"
  SOURCE_DESC="deploy_env/hotfix/bundled_skills（本包自带，全量版）"
elif docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  SOURCE_DIR="$(mktemp -d)"
  trap 'rm -rf "$SOURCE_DIR"' EXIT
  docker cp "$BACKEND_CONTAINER:/app/bundled_skills/." "$SOURCE_DIR/" >/dev/null
  SOURCE_DESC="容器 $BACKEND_CONTAINER:/app/bundled_skills（注意：可能是镜像内精简兜底版）"
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

# ------------------------------------------------------ 源自检（全量版特征）
src_rules="$SOURCE_DIR/$SKILL_NAME/references/review-rules.md"
src_skill="$SOURCE_DIR/$SKILL_NAME/SKILL.md"
if [ ! -f "$src_rules" ] || [ ! -f "$src_skill" ]; then
  echo "[错误] 技能源不完整，缺少 $SKILL_NAME 的 SKILL.md 或 references/review-rules.md" >&2
  exit 1
fi
if grep -qF "$RULES_SIGNATURE" "$src_rules" && grep -qF "$SKILL_SIGNATURE" "$src_skill"; then
  echo "[源检查] 全量版 ✓  $(wc -c < "$src_skill" | tr -d ' ') B SKILL.md / $(wc -c < "$src_rules" | tr -d ' ') B review-rules.md"
else
  echo "[源检查] ⚠️  源不含全量版特征串（$RULES_SIGNATURE / $SKILL_SIGNATURE），疑似精简版。" >&2
  echo "         本包应自带全量版；若从容器回退取源，说明镜像里仍是精简兜底版。" >&2
  echo "         继续同步会把精简版推到宿主机目录，05-verify.sh 的全量版断言将失败。" >&2
  if [ "$APPLY" = 1 ]; then exit 1; fi
fi
echo

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

# --------------------------------------------------- 目标版本辨认（只读）
tgt_skill="$SKILLS_DIR/$SKILL_NAME/SKILL.md"
tgt_rules="$SKILLS_DIR/$SKILL_NAME/references/review-rules.md"
if [ -f "$tgt_skill" ] && [ -f "$tgt_rules" ]; then
  if grep -qF "$RULES_SIGNATURE" "$tgt_rules"; then
    echo "[版本辨认] 目标是全量版（$(wc -c < "$tgt_skill" | tr -d ' ') B SKILL.md / $(wc -c < "$tgt_rules" | tr -d ' ') B review-rules.md）"
  else
    echo "[版本辨认] ⚠️  目标是**精简兜底版**（$(wc -c < "$tgt_skill" | tr -d ' ') B SKILL.md / $(wc -c < "$tgt_rules" | tr -d ' ') B review-rules.md），缺少「$RULES_SIGNATURE」等章节。"
    echo "           升级到全量版：bash $(basename "${BASH_SOURCE[0]}") --apply --force"
  fi
elif [ -f "$tgt_skill" ]; then
  echo "[版本辨认] 目标目录缺少 references/review-rules.md（残缺），建议 --apply 补齐"
else
  echo "[版本辨认] 目标尚无该技能，--apply 将新建"
fi
echo

# ------------------------------------------------------- zip（商店安装用）
zip_state="无"
if [ -f "$ZIP_SRC" ]; then
  zip_dst="$SKILLS_DIR/test-case-clarity-review.zip"
  if [ ! -f "$zip_dst" ]; then zip_state="缺失，将写入"
  elif cmp -s "$ZIP_SRC" "$zip_dst"; then zip_state="已一致"
  else zip_state="内容不同$( [ "$FORCE" = 1 ] && echo '，将覆盖' || echo '，默认不动（加 --force 覆盖）')"
  fi
  echo "[商店包] $ZIP_SRC（$(wc -c < "$ZIP_SRC" | tr -d ' ') B）→ $zip_state"
else
  echo "[商店包] 本包未附带 $ZIP_SRC，跳过（不影响 05-verify.sh）"
fi
echo

if [ "$APPLY" != 1 ]; then
  echo "[只诊断] 未改动任何文件。确认上面清单后再执行：bash $(basename "${BASH_SOURCE[0]}") --apply"
  [ "$differ" -gt 0 ] && echo "         注意有 $differ 个文件内容不同，默认不会被覆盖；升级为全量版请加 --force。"
  exit 0
fi

# ------------------------------------------------------------- 写动作
to_copy="$missing"
[ "$FORCE" = 1 ] && to_copy=$((missing + differ))
zip_write=0
if [ -f "$ZIP_SRC" ] && [ ! -f "$SKILLS_DIR/test-case-clarity-review.zip" ]; then zip_write=1; fi
[ -f "$ZIP_SRC" ] && [ "$FORCE" = 1 ] && zip_write=1

skip_write=0
if [ "$to_copy" -eq 0 ] && [ "$zip_write" -eq 0 ]; then
  echo "[完成] 技能文件无需写入：目标已是最新（缺失 0，且$([ "$FORCE" = 1 ] && echo '内容全部一致' || echo '未启用覆盖')）。"
  echo "       继续检查 manifest 条目（如已存在且一致则为 SKIP）。"
  skip_write=1
fi

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
if [ "$skip_write" = 0 ]; then
  if [ "$missing_dir" = 0 ]; then
    backup="$BACKUP_DIR/skills-backup-$stamp.tar.gz"
    tar czf "$backup" -C "$SKILLS_DIR" . 2>/dev/null
    echo "[备份] $backup（$(du -h "$backup" | awk '{print $1}')）"
  else
    echo "[备份] 跳过技能目录（原不存在，无内容可备份）"
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

  if [ "$zip_write" = 1 ]; then
    cp -p "$ZIP_SRC" "$SKILLS_DIR/test-case-clarity-review.zip"
    echo "  [写入] test-case-clarity-review.zip（商店安装用）"
  fi

  chmod -R a+rX "$SKILLS_DIR" 2>/dev/null || true
  echo
  echo "[完成] 已写入 $copied 个技能文件$([ "$zip_write" = 1 ] && echo ' + 1 个商店 zip')。"
fi

# ------------------------------------------------- manifest.json 条目合并
if [ "$MANIFEST" = 1 ]; then
  echo
  echo "--- manifest.json 条目 ---"
  MF="$SKILLS_DIR/manifest.json"
  if [ ! -f "$MF" ]; then
    echo "  [跳过] $MF 不存在（无商店清单，不影响 06/05 校验）"
  else
    MERGE_PY="$(mktemp)"
    trap 'rm -f "$MERGE_PY"' EXIT
    # 条目完全从 SKILL.md 的 frontmatter 派生，不在脚本里抄一份 description，
    # 避免又出现「同一技能多份描述互相漂移」的老问题。
    cat > "$MERGE_PY" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

manifest_path, skill_dir, version = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(skill_dir, "SKILL.md").read_text(encoding="utf-8")
front = text.split("---", 2)[1]
name = re.search(r"^name:\s*(.+?)\s*$", front, re.M).group(1).strip().strip('"').strip("'")
desc = re.search(r"^description:\s*(.+?)\s*$", front, re.M).group(1).strip().strip('"').strip("'")
entry = {
    "id": name,
    "name": name,
    "description": desc,
    "version": version,
    "author": "mgdaas-lab",
    "tags": ["testcase", "review", "quality"],
    "zip_path": f"{name}.zip",
}
with open(manifest_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
skills = data.setdefault("skills", [])
for idx, item in enumerate(skills):
    if item.get("id") == entry["id"]:
        if item == entry:
            print("SKIP", idx + 1)
        else:
            skills[idx] = entry
            print("UPDATE", idx + 1)
        break
else:
    skills.append(entry)
    print("ADD", len(skills))
with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PYEOF
    cp -p "$MF" "$BACKUP_DIR/manifest-backup-$stamp.json"
    merge_result=""
    if command -v python3 >/dev/null 2>&1; then
      merge_result="$(python3 "$MERGE_PY" "$MF" "$SKILLS_DIR/$SKILL_NAME" "1.0.0" 2>&1)" || merge_result=""
    elif docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
      merge_result="$(docker exec -i "$BACKEND_CONTAINER" python3 - \
        "/app/bundled_skills/manifest.json" "/app/bundled_skills/$SKILL_NAME" "1.0.0" < "$MERGE_PY" 2>&1)" || merge_result=""
    fi
    rm -f "$MERGE_PY"
    case "$merge_result" in
      ADD*|UPDATE*|SKIP*)
        echo "  [通过] 条目已处理：$merge_result（备份 $BACKUP_DIR/manifest-backup-$stamp.json）" ;;
      *)
        echo "  [提示] 未自动合并（本机无 python3，容器 $BACKEND_CONTAINER 也不可用）。" >&2
        echo "         不影响 05-verify.sh 与技能生效；如需清单完整，请按下方片段手工追加到 $MF 的 skills 数组：" >&2
        echo '         {"id":"test-case-clarity-review","name":"test-case-clarity-review","description":"<同 SKILL.md frontmatter>","version":"1.0.0","author":"mgdaas-lab","tags":["testcase","review","quality"],"zip_path":"test-case-clarity-review.zip"}' >&2 ;;
    esac
  fi
fi

# ---------------------------------------------- 刷新数据库里的技能内容
# 审查时送进模型的是 **数据库里 Skill 的 skill_content**（build_skill_snapshot 优先走 DB，
# 并把 media 目录下的 references/*.md 内联进去）；宿主机目录只是技能来源。DB 内容由容器
# 启动时的 init_skills 从该目录同步 —— 所以只改目录不刷新 DB，界面与审查用的仍是旧版。
echo
echo "--- 数据库技能刷新（init_skills）---"
if [ "$DB_SYNC" != 1 ]; then
  echo "  [跳过] 已指定 --no-db-sync；DB 内容将在下次 Backend 启动时由 entrypoint 的 init_skills 刷新"
elif ! docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  echo "  [跳过] 容器 $BACKEND_CONTAINER 不在运行；下次启动 Backend 时 entrypoint 会自动执行 init_skills"
else
  if docker exec "$BACKEND_CONTAINER" /opt/venv/bin/python /app/manage.py init_skills 2>&1 | sed 's/^/      /'; then
    echo "  [通过] 已刷新数据库技能内容与 media 目录下的技能文件（等价于重启 Backend，但不中断服务）"
  else
    echo "  [警告] init_skills 执行失败，可手动重试：" >&2
    echo "         docker exec $BACKEND_CONTAINER /opt/venv/bin/python /app/manage.py init_skills" >&2
    echo "         或在平台「Skill 管理」直接上传 offline-images/skills/test-case-clarity-review.zip 覆盖安装。" >&2
  fi
fi

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

if [ -f "$tgt_rules" ] && grep -qF "$RULES_SIGNATURE" "$tgt_rules"; then
  echo "  [通过] 目标是全量版（含「$RULES_SIGNATURE」）"
else
  echo "  [失败] 目标仍非全量版；如原为旧版/精简版，请用 --apply --force 覆盖" >&2
  exit 1
fi

if docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  if docker exec "$BACKEND_CONTAINER" test -f /app/bundled_skills/$SKILL_NAME/references/review-rules.md; then
    echo "  [通过] 容器内 /app/bundled_skills/$SKILL_NAME/references/review-rules.md 已可见（只读挂载即时生效）"
  else
    echo "  [失败] 容器内仍看不到该文件，请检查挂载：docker inspect $BACKEND_CONTAINER --format '{{range .Mounts}}{{println .Source \" -> \" .Destination}}{{end}}'" >&2
    exit 1
  fi
  if docker exec "$BACKEND_CONTAINER" grep -qF "$RULES_SIGNATURE" /app/bundled_skills/$SKILL_NAME/references/review-rules.md 2>/dev/null; then
    echo "  [通过] 容器内看到的就是全量版内容"
  else
    echo "  [失败] 容器内仍非全量版，请确认挂载源是否就是 $SKILLS_DIR" >&2
    exit 1
  fi
else
  echo "  [跳过] 容器 $BACKEND_CONTAINER 不在运行，容器内可见性未校验"
fi
echo
echo "[下一步] 重跑校验：bash $UPDATE_DIR/05-verify.sh"
