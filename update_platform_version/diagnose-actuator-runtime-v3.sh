#!/usr/bin/env bash
set -u

ROOT_DIR="${1:-/projects/ai-test-platform}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${ROOT_DIR}/update_platform_version/actuator-dispatch-${STAMP}.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

section() {
  echo
  echo "========== $1 =========="
}

run() {
  echo "+ $*"
  "$@" 2>&1 || echo "[WARN] 命令失败，退出码: $?"
}

echo "执行器任务下发诊断"
echo "时间: $(date '+%F %T %z')"
echo "主机: $(hostname)"
echo "根目录: $ROOT_DIR"

section "相关容器状态"
run docker ps -a --filter name=wharttest --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'

for name in wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  section "$name 基本信息"
  run docker inspect "$name" --format '状态={{.State.Status}} 重启次数={{.RestartCount}} 启动={{.State.StartedAt}} 退出={{.State.ExitCode}} 错误={{.State.Error}}'
  run docker inspect "$name" --format 'Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}'
  run docker inspect "$name" --format '日志驱动={{.HostConfig.LogConfig.Type}} 日志配置={{json .HostConfig.LogConfig.Config}}'
  run docker inspect "$name" --format '网络={{range $k,$v := .NetworkSettings.Networks}}{{$k}} IP={{$v.IPAddress}} {{end}}'

  section "$name 进程"
  run docker top "$name" -eo pid,ppid,user,stat,etime,args

  section "$name PID 1 输出与运行目录"
  run docker exec "$name" sh -lc 'echo "cmdline=$(tr "\000" " " </proc/1/cmdline)"; echo "cwd=$(readlink /proc/1/cwd)"; echo "stdout=$(readlink /proc/1/fd/1)"; echo "stderr=$(readlink /proc/1/fd/2)"'

  section "$name 执行器环境（隐藏密码）"
  docker inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>&1 \
    | grep -E '^(WHARTTEST_ACTUATOR_|PYTHONUNBUFFERED|LOG)' \
    | sed -E 's/(PASSWORD|TOKEN|SECRET|KEY)=.*/\1=***隐藏***/I' \
    || true

  section "$name 到后端连通性"
  run docker exec "$name" python -c 'import socket; print("backend ->", socket.gethostbyname("backend")); s=socket.create_connection(("backend",8000),5); print("backend:8000 TCP OK"); s.close()'

  section "$name 到被测网站连通性"
  run docker exec "$name" python -c 'import urllib.request; r=urllib.request.urlopen("http://www.test.sse.com.cn", timeout=15); print("status=", r.status, "url=", r.url)'

  section "$name 最近日志"
  run docker logs --timestamps --tail 300 "$name"
done

section "后端状态与网络"
run docker inspect wharttest-backend --format '状态={{.State.Status}} 重启次数={{.RestartCount}} 网络={{range $k,$v := .NetworkSettings.Networks}}{{$k}} IP={{$v.IPAddress}} {{end}}'
run docker top wharttest-backend -eo pid,ppid,user,stat,etime,args

section "后端最近日志（完整尾部）"
run docker logs --timestamps --tail 800 wharttest-backend

section "容器网络归属对比"
for name in wharttest-backend wharttest-actuator-01 wharttest-actuator-02 wharttest-actuator-03; do
  docker inspect "$name" --format '{{.Name}}: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>&1 || true
done

section "诊断结束"
echo "说明：平台显示执行器在线且槽位被占用，即表示连接和任务下发已发生。"
echo "请将本日志完整提供，不要只截取最后几行。"
echo "日志文件: $LOG_FILE"
