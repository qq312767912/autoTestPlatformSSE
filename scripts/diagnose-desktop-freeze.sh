#!/usr/bin/env bash
# ============================================================================
# 信创终端「桌面冻结」现场取证脚本（只读，不修改任何系统配置）
#
# 用途：盒子出现「鼠标键盘无响应、但后台任务仍在跑」时，在盒子本机或通过
#       SSH 执行，采集现场状态，用于区分以下三条链路：
#         A. 显示栈挂死（Xorg/Wayland 合成器 + GPU 驱动）
#         B. 内存压力导致的 swap 抖动
#         C. 磁盘 IO 饱和导致的进程饿死
#
# 用法：  bash diagnose-desktop-freeze.sh
# 输出：  /tmp/desktop-freeze-<时间戳>.log
# 提示：  卡死状态下如果 SSH 进得来，直接跑本脚本，不要重启盒子。
# ============================================================================

set -u

OUT="/tmp/desktop-freeze-$(date +%Y%m%d-%H%M%S).log"

section() { printf '\n===== %s =====\n' "$1" >>"$OUT"; }

run() {
    # 执行命令并追加输出，失败不中断
    printf '$ %s\n' "$*" >>"$OUT"
    eval "$*" >>"$OUT" 2>&1 || printf '(命令不可用或失败)\n' >>"$OUT"
}

# ----------------------------------------------------------------------------
section "1. 基本信息"
run "date -Is"
run "uname -a"
run "grep -E 'PRETTY_NAME|VERSION=' /etc/os-release"
run "echo \"架构: \$(uname -m)\""
run "echo \"会话类型: \${XDG_SESSION_TYPE:-unknown}\""
run "echo \"桌面环境: \${XDG_CURRENT_DESKTOP:-unknown}\""
run "uptime"

# ----------------------------------------------------------------------------
section "2. 显卡与渲染后端（关键判据）"
# 若 renderer 显示 llvmpipe / softpipe，说明完全没有 GPU 加速，
# 此时全屏 canvas 动画 + 大面积 backdrop-filter 会全部压在 CPU 上。
if command -v glxinfo >/dev/null 2>&1; then
    run "glxinfo -B 2>/dev/null | grep -iE 'OpenGL renderer|OpenGL version|Device'"
else
    printf 'glxinfo 未安装（麒麟/统信可 apt install mesa-utils）\n' >>"$OUT"
    run "ls -l /dev/dri/"
fi
run "lsmod | grep -iE 'nvidia|amdgpu|radeon|i915|mali|panfrost|phytium|jmv|mthreads|zhaoxin' || echo '未匹配到已知显卡驱动模块'"
run "ls -l /dev/dri/by-path/ 2>/dev/null || true"

# ----------------------------------------------------------------------------
section "3. 内核日志中的显卡/挂死痕迹（核心证据）"
run "dmesg -T 2>/dev/null | grep -iE 'gpu|drm|hang|reset|stall|Xorg|wayland|kwin|mutter' | tail -60 || echo 'dmesg 需要 root 权限'"
run "journalctl -b -p 3 --no-pager 2>/dev/null | tail -60 || echo 'journalctl 不可用'"
run "journalctl -b --no-pager 2>/dev/null | grep -iE 'gpu reset|hung task|blocked for more than|soft lockup|Out of memory|oom-kill' | tail -30 || true"

# ----------------------------------------------------------------------------
section "4. 进程状态（谁在占用 CPU、谁卡在不可中断睡眠）"
run "ps -eo pid,stat,pcpu,pmem,rss,etime,comm --sort=-pcpu | head -20"
run "echo '--- D 状态（不可中断睡眠）进程 ---'"
run "ps -eo pid,stat,wchan:24,comm | awk '\$2 ~ /D/' || echo '无 D 状态进程'"
run "echo '--- 桌面/浏览器进程 ---'"
run "pgrep -a -f 'Xorg|Xwayland|kwin|mutter|compiz|firefox|chrome|chromium|360|qaxbrowser|browser' | head -20 || echo '未匹配'"

# ----------------------------------------------------------------------------
section "5. 内存与 swap（判据 B）"
run "free -h"
run "cat /proc/pressure/memory 2>/dev/null || echo '无 PSI 支持'"
run "vmstat 1 5"
run "echo '--- 内存占用前 10 ---'"
run "ps -eo pid,rss,comm --sort=-rss | head -11"

# ----------------------------------------------------------------------------
section "6. 磁盘 IO（判据 C）"
if command -v iostat >/dev/null 2>&1; then
    run "iostat -x 1 3"
else
    printf 'iostat 未安装（sysstat 包）\n' >>"$OUT"
    run "cat /proc/pressure/io 2>/dev/null || echo '无 PSI 支持'"
    run "cat /proc/diskstats"
fi
run "df -h"

# ----------------------------------------------------------------------------
section "7. 输入子系统"
run "ls -l /dev/input/by-path/ 2>/dev/null | grep -iE 'kbd|mouse|event' || echo '无法列举'"
run "grep -E '^N:|^H:' /proc/bus/input/devices 2>/dev/null | head -40"
if command -v evtest >/dev/null 2>&1; then
    printf '提示：可另开终端执行 evtest，敲键盘看是否有事件输出。\n' >>"$OUT"
    printf '      有事件但屏幕不动 = 合成器挂死；无事件 = 输入子系统/驱动层。\n' >>"$OUT"
fi

# ----------------------------------------------------------------------------
section "8. 自动化测试平台容器状态"
if command -v docker >/dev/null 2>&1; then
    run "docker ps --format 'table {{.Names}}\t{{.Status}}'"
    run "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | head -15"
else
    printf 'docker 不在 PATH（本平台通常在 /usr/bin/docker）\n' >>"$OUT"
fi

# ----------------------------------------------------------------------------
section "9. 自动初判"
{
    renderer=$(glxinfo -B 2>/dev/null | grep -i 'OpenGL renderer' | head -1)
    case "$renderer" in
        *llvmpipe*|*softpipe*|*swrast*)
            echo "[!] 软件渲染：$renderer"
            echo "    → 无 GPU 加速，登录页全屏动画与大半径模糊全部由 CPU 承担，"
            echo "      合成线程极易堆积导致桌面假死。符合判据 A。"
            ;;
        "") echo "[-] 未能获取渲染后端，请检查 glxinfo 是否安装。" ;;
        *)  echo "[+] 硬件渲染：$renderer" ;;
    esac

    if dmesg 2>/dev/null | grep -qiE 'gpu reset|drm.*hang|blocked for more than'; then
        echo "[!] 内核日志出现 GPU reset / hung task 痕迹，符合判据 A。"
    fi

    avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo 2>/dev/null)
    [ -n "${avail:-}" ] && [ "$avail" -lt 1024 ] && \
        echo "[!] 可用内存仅 ${avail} MB，存在 swap 抖动风险，符合判据 B。"
} >>"$OUT"

printf '\n取证完成，报告已保存：%s\n' "$OUT"
printf '请把该文件回传分析。\n'
