#!/usr/bin/env bash

# WHartTest Skill 丢失只读排查脚本。
# 用法：
#   bash deploy-scripts/diagnose-skills.sh
#   bash deploy-scripts/diagnose-skills.sh <backend容器名或ID>

set -u

section() {
  echo
  echo "========== $1 =========="
}

fail() {
  echo "[失败] $1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未找到 docker 命令"
docker info >/dev/null 2>&1 || fail "无法访问 Docker，请检查 Docker 服务或当前用户权限"

backend_container="${1:-}"

if [ -z "$backend_container" ]; then
  backend_candidates="$(docker ps \
    --filter label=com.docker.compose.service=backend \
    --format '{{.ID}} {{.Names}}')"

  candidate_count="$(printf '%s\n' "$backend_candidates" | awk 'NF {count++} END {print count+0}')"
  if [ "$candidate_count" -eq 0 ]; then
    fail "没有找到带 com.docker.compose.service=backend 标签的运行中容器。请将容器名作为第一个参数传入"
  fi
  if [ "$candidate_count" -gt 1 ]; then
    echo "$backend_candidates"
    fail "找到多个 backend 容器，请将目标容器名作为第一个参数传入"
  fi
  backend_container="$(printf '%s\n' "$backend_candidates" | awk 'NF {print $1; exit}')"
fi

docker inspect "$backend_container" >/dev/null 2>&1 || fail "容器不存在：$backend_container"

section "Backend 容器"
docker inspect "$backend_container" --format \
  '名称={{.Name}} 状态={{.State.Status}} 镜像={{.Config.Image}} 启动时间={{.State.StartedAt}}'

section "Compose 标识"
docker inspect "$backend_container" --format \
  '项目={{index .Config.Labels "com.docker.compose.project"}} 服务={{index .Config.Labels "com.docker.compose.service"}} 工作目录={{index .Config.Labels "com.docker.compose.project.working_dir"}} 配置文件={{index .Config.Labels "com.docker.compose.project.config_files"}}'

section "Backend 挂载"
docker inspect "$backend_container" --format \
  '{{range .Mounts}}{{println .Destination " <- " .Source " 类型=" .Type " 卷名=" .Name}}{{end}}'

section "关键环境变量"
docker inspect "$backend_container" --format '{{range .Config.Env}}{{println .}}{{end}}' |
  awk '/^(MEDIA_ROOT|DATABASE_TYPE|DATABASE_PATH|POSTGRES_HOST|POSTGRES_DB|BUNDLED_SKILLS_DIR)=/'

section "Skill 数据库记录与文件状态"
docker exec -w /app "$backend_container" python manage.py shell -c '
import os
from skills.models import Skill

skills = list(Skill.objects.select_related("project").order_by("id"))
missing = 0
inactive = 0
for skill in skills:
    path = skill.get_full_path() or ""
    exists = bool(path and os.path.isdir(path))
    skill_md = bool(exists and os.path.isfile(os.path.join(path, "SKILL.md")))
    missing += int(not skill_md)
    inactive += int(not skill.is_active)
    print(
        f"id={skill.id} project={skill.project_id} name={skill.name} "
        f"active={skill.is_active} dir={exists} SKILL.md={skill_md} path={path}"
    )
print(f"SUMMARY total={len(skills)} active={len(skills)-inactive} inactive={inactive} missing={missing}")
' || echo "[异常] 无法查询 Skill 数据库记录，请检查后端日志和数据库连接"

section "容器内实际 Skill 文件"
docker exec "$backend_container" sh -c '
media_root=${MEDIA_ROOT:-/app/data/media}
echo "MEDIA_ROOT=$media_root"
if [ -d "$media_root/skills" ]; then
  find "$media_root/skills" -type f -name SKILL.md -print | sort
  count=$(find "$media_root/skills" -type f -name SKILL.md | wc -l | tr -d " ")
  echo "FILESYSTEM_SKILL_COUNT=$count"
else
  echo "[异常] 目录不存在：$media_root/skills"
fi
'

section "预置 Skill 挂载"
docker exec "$backend_container" sh -c '
bundled_dir=${BUNDLED_SKILLS_DIR:-/app/bundled_skills}
echo "BUNDLED_SKILLS_DIR=$bundled_dir"
if [ -d "$bundled_dir" ]; then
  find "$bundled_dir" -type f -name SKILL.md -print | sort
  count=$(find "$bundled_dir" -type f -name SKILL.md | wc -l | tr -d " ")
  echo "BUNDLED_SKILL_COUNT=$count"
else
  echo "[提示] 预置 Skill 目录未挂载；这不影响已经通过前端上传并持久化的 Skill"
fi
'

section "PostgreSQL 容器与数据卷"
postgres_candidates="$(docker ps -a \
  --filter label=com.docker.compose.service=postgres \
  --format '{{.ID}} {{.Names}} {{.Status}}')"
if [ -n "$postgres_candidates" ]; then
  echo "$postgres_candidates"
  while IFS= read -r postgres_line; do
    [ -n "$postgres_line" ] || continue
    postgres_id="$(printf '%s\n' "$postgres_line" | awk '{print $1}')"
    docker inspect "$postgres_id" --format \
      '{{range .Mounts}}{{println .Destination " <- " .Source " 类型=" .Type " 卷名=" .Name}}{{end}}'
  done <<EOF
$postgres_candidates
EOF
else
  echo "[提示] 未找到带 Compose postgres 服务标签的容器"
fi

section "最近24小时 Skill 相关日志"
docker logs --since 24h "$backend_container" 2>&1 |
  grep -Ei 'skill|traceback|permission denied|no such file' |
  tail -n 120 || true

section "判断方法"
echo "1. SUMMARY total=0：优先检查 PostgreSQL 是否换了数据卷或 Compose 项目名。"
echo "2. total>0 且 missing>0：数据库记录存在，但 /app/data/media/skills 文件丢失或挂载路径变化。"
echo "3. total>0 且 missing=0：后端数据正常，检查 Skill 是否 inactive，以及前端/API错误。"
echo "4. 重建容器后才消失：将 ./data 改为固定绝对路径，例如 /opt/wharttest/data:/app/data。"
echo "5. 本脚本仅执行读取操作，不会修改容器、数据库、镜像或数据卷。"
