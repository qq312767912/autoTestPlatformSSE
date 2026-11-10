#!/usr/bin/env bash
# 只读检查：不输出环境变量值或密钥，不修改容器。
set -euo pipefail

command -v docker >/dev/null || { echo '未找到 Docker' >&2; exit 2; }
command -v python3 >/dev/null || { echo '未找到 Python 3' >&2; exit 2; }

docker inspect wharttest-backend wharttest-crg | python3 -c '
import json, sys
items = json.load(sys.stdin)
for item in items:
    name = item.get("Name", "").lstrip("/")
    env = dict(part.split("=", 1) for part in item.get("Config", {}).get("Env", []) if "=" in part)
    state = item.get("State", {})
    health = (state.get("Health") or {}).get("Status", "未配置")
    labels = item.get("Config", {}).get("Labels") or {}
    image = item.get("Image")
    status = state.get("Status")
    print(f"[{name}] 镜像ID={image} 状态={status} 健康={health}")
    print("Compose项目=", labels.get("com.docker.compose.project", "未标记"))
    print("Compose文件=", labels.get("com.docker.compose.project.config_files", "未标记"))
    print("Compose配置哈希=", labels.get("com.docker.compose.config-hash", "未标记"))
    for key in ("CODE_REVIEW_GRAPH_ENABLED", "CODE_REVIEW_GRAPH_URL", "CODE_REVIEW_GRAPH_TOKEN", "CRG_INTERNAL_TOKEN", "CRG_TOKEN_FILE"):
        present = "已配置" if env.get(key) else "未配置"
        print(f"{key}: {present}")
    print()
'

for path in \
  /projects/ai-test-platform/offline-images/docker-compose.offline.yml \
  /projects/ai-test-platform/update_platform_version/deploy_env/docker-compose.update.yml \
  /projects/ai-test-platform/update_platform_version/deploy_env/secrets/crg_internal_token; do
  if [ -s "$path" ]; then
    printf '文件存在且非空：%s\n' "$path"
  elif [ -e "$path" ]; then
    printf '文件存在但为空：%s\n' "$path"
  else
    printf '文件不存在：%s\n' "$path"
  fi
done
echo '只读检查完成；没有输出任何 Token 值。'
