#!/usr/bin/env bash
set -euo pipefail

BASE_COMPOSE="${BASE_COMPOSE:-/projects/ai-test-platform/offline-images/docker-compose.offline.yml}"
UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERRIDE_COMPOSE="$UPDATE_DIR/docker-compose.update.yml"

[ -f "$UPDATE_DIR/.latest-backup" ] || { echo "未发现升级前备份，请先执行 02-backup.sh" >&2; exit 1; }
backup_dir="$(cat "$UPDATE_DIR/.latest-backup")"
[ -f "$backup_dir/BACKUP_COMPLETE" ] || { echo "最近一次备份不完整：$backup_dir" >&2; exit 1; }

compose=(docker compose -p offline-images -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE")
"${compose[@]}" config >/dev/null

wait_healthy() {
  local container="$1"
  local limit="${2:-180}"
  local elapsed=0
  while [ "$elapsed" -lt "$limit" ]; do
    status="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' 2>/dev/null || true)"
    case "$status" in
      healthy|running) echo "[通过] $container: $status"; return 0 ;;
      # 容器初次启动会执行数据库迁移、技能同步和超时任务收尾，
      # healthcheck 在此期间可能短暂进入 unhealthy，应继续等待至上限。
      unhealthy) ;;
      stopped) docker logs --tail 100 "$container"; return 1 ;;
    esac
    sleep 5
    elapsed=$((elapsed + 5))
  done
  docker logs --tail 100 "$container" || true
  echo "$container 健康检查超时" >&2
  return 1
}

# ------------------------------------------------------------------ 内置技能
# 容器内 /app/bundled_skills 由基础 compose 的 `./skills:/app/bundled_skills:ro`
# 挂载自宿主机外置目录——不在镜像内，也不随升级包自动落地，必须在这里补齐，
# 否则 05-verify.sh 的技能断言必然失败。
# 放在 up 之前：Backend 启动时 entrypoint 会自动执行 init_skills，
# 先刷好宿主机目录，容器第一次起来读到的就是包内版本。
SKILLS_SYNC="$UPDATE_DIR/24-sync-bundled-skills.sh"

if [ -f "$SKILLS_SYNC" ]; then
  echo "[技能] 同步内置技能到宿主机外置目录（写前自动整目录备份，可回退）"
  # 这里【故意不传 SKILLS_DIR】：目标目录由 24 自己从 BASE_COMPOSE 推导。
  # 24 用「SKILLS_DIR 是否由外部给定」来区分“自动推导”和“用户手动指定”，
  # 自动推导时才会把「推导结果 vs 容器实际挂载源」当硬校验（不一致即中止）。
  # 若由本脚本代传，24 会误判成用户手动指定，从而绕过该校验。
  # 用户 export SKILLS_DIR 时仍会被 24 继承，并正确进入“手动指定”语义。
  SKILLS_SYNC_BY_DEPLOY=1 BASE_COMPOSE="$BASE_COMPOSE" UPDATE_DIR="$UPDATE_DIR" \
    bash "$SKILLS_SYNC" --apply --force
else
  echo "[警告] 未找到 $SKILLS_SYNC，跳过内置技能同步；05-verify.sh 的技能断言可能失败" >&2
fi

echo "[升级] 替换 Backend 和 Frontend（Backend 入口脚本会执行数据库迁移）"
"${compose[@]}" up -d --pull never --no-deps backend frontend
wait_healthy wharttest-backend 300
wait_healthy wharttest-frontend 180

# 刷新数据库中的技能快照。
# Backend 的 entrypoint 只在容器「被重建」时才跑 init_skills；只换技能文件、不换镜像时
# 容器不会重建，数据库里仍是旧内容——而审查送进模型的正是 Skill.skill_content。
# init_skills 对已存在技能是「覆盖更新」且保留 is_active，故这里无条件补一次，幂等安全。
echo "[技能] 刷新数据库中的技能快照"
if docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills; then
  echo "[通过] 技能快照已刷新"
else
  echo "[失败] init_skills 执行失败，请排查后单独重跑：" >&2
  echo "       docker exec wharttest-backend /opt/venv/bin/python /app/manage.py init_skills" >&2
  exit 1
fi

echo "[恢复] Backend 网络恢复后重新拉起三个执行器"
"${compose[@]}" up -d --pull never --no-deps actuator-01 actuator-02 actuator-03
wait_healthy wharttest-actuator-01 180
wait_healthy wharttest-actuator-02 180
wait_healthy wharttest-actuator-03 180

echo "Backend、Frontend 升级完成，三个执行器已恢复；Vision MCP 保持运行。"
echo "请执行 05-verify.sh，并进行登录、代码审查、用例审查等人工验收。"
