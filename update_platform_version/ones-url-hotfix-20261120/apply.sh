#!/usr/bin/env bash
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
C=wharttest-backend
[[ "$(docker inspect --format '{{.Config.Image}}' "$C")" == wharttest-250-backend:update-20261119-89ffd9ef-r2-arm64 ]] || { echo '镜像版本与补丁不匹配，停止';exit 1; }
# solo消费者忙碌时可能无法响应；无响应或有运行任务均停止，不强行中断。
timeout 25 docker exec "$C" python -c 'from wharttest_django.celery import app; r=app.control.inspect(timeout=5).active(); assert r and not any(r.values()), "存在运行任务或消费者未响应，请暂停提交并等待任务结束"'
# 先验证目标HTTPS链路；证书错误不绕过。
docker exec "$C" python -c 'import requests; r=requests.get("https://10.122.212.49:60000/project/api/project/auth/login",timeout=(5,10),allow_redirects=False,stream=True); print("ONES HTTP状态：",r.status_code); assert r.status_code in (200,400,401,403,405), "ONES接口未就绪"; r.close()'
B="$HERE/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$B"
docker inspect --format '{{.Id}}' "$C" > "$B/container-id"
docker cp "$C:/app/knowledge/ones_import.py" "$B/ones_import.py"
python3 - "$B" "$HERE/expected.sha256" <<'PY'
import pathlib,sys,hashlib
p=pathlib.Path(sys.argv[1]);s=(p/'ones_import.py').read_text();assert hashlib.sha256((p/'ones_import.py').read_bytes()).hexdigest()==pathlib.Path(sys.argv[2]).read_text().strip(), '目标源码已变化，停止以保护其他改动';old="base = 'https://ones.sseinfo.com:60000'";new="base = 'https://10.122.212.49:60000'"
assert s.count(old)==1,'目标文件不是预期版本或已修复，停止'
s=s.replace(old,new,1);compile(s,'ones_import.py','exec');(p/'patched.py').write_text(s)
PY
docker cp "$B/patched.py" "$C:/tmp/ones-url-preflight.py"
docker exec "$C" python -c 'compile(open("/tmp/ones-url-preflight.py").read(),"ones_import.py","exec")'
trap 'echo "补丁失败，自动回滚"; bash "$HERE/rollback.sh" "$B"' ERR
docker cp "$B/patched.py" "$C:/app/knowledge/ones_import.py"
docker exec "$C" python -c 'import os;os.environ.setdefault("DJANGO_SETTINGS_MODULE","wharttest_django.settings");import django;django.setup();from knowledge.ones_import import OnesClient;assert OnesClient.base=="https://10.122.212.49:60000"'
docker restart -t 240 "$C"
for ((i=0;i<90;i++)); do
 state=$(docker inspect --format '{{.State.Health.Status}}' "$C")
 if [[ "$state" == healthy ]]; then trap - ERR; echo "地址补丁完成。备份：$B";exit 0;fi
 sleep 10
done
false
