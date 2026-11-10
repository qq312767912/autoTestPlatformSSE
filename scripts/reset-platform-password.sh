#!/usr/bin/env bash
# Authorized administrator use. Password entered only via Django hidden prompt.
set -euo pipefail
container="${BACKEND_CONTAINER:-wharttest-backend}"
command -v docker >/dev/null || { echo '未找到 Docker'; exit 1; }
[ -t 0 ] && [ -t 1 ] || { echo '请在交互终端运行，不能通过管道传入密码。'; exit 1; }
read -r -p '请输入需要重置密码的账号名：' account
[ -n "$account" ] || { echo '账号名不能为空'; exit 1; }
docker exec -i "$container" /opt/venv/bin/python - "$account" <<'PY'
import os,sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','wharttest_django.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User=get_user_model()
try:
 user=User._default_manager.get(**{User.USERNAME_FIELD:sys.argv[1]})
except User.DoesNotExist:
 print('账号不存在，未做任何修改。');sys.exit(1)
print('账号已找到：',user.get_username(),'；启用状态：',user.is_active)
PY
read -r -p '确认重置此账号密码？输入 RESET 继续：' confirmation
[ "$confirmation" = RESET ] || { echo '已取消，未修改密码。'; exit 0; }
# No password in shell history, arguments, environment, or script logs.
docker exec -it "$container" /opt/venv/bin/python /app/manage.py changepassword -- "$account"
