#!/bin/sh
set -eu

DISPLAY_NUMBER="${RECORDER_DESKTOP_DISPLAY:-:99}"
SCREEN_SIZE="${RECORDER_DESKTOP_SCREEN_SIZE:-1400x900x24}"
VNC_PORT="${RECORDER_DESKTOP_VNC_PORT:-5900}"
WEB_PORT="${RECORDER_DESKTOP_WEB_PORT:-6080}"

export DISPLAY="$DISPLAY_NUMBER"

cleanup() {
  for pid in ${WEBSOCKIFY_PID:-} ${VNC_PID:-} ${WM_PID:-} ${XVFB_PID:-}; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

rm -f "/tmp/.X${DISPLAY_NUMBER#:}-lock" "/tmp/.X11-unix/X${DISPLAY_NUMBER#:}" 2>/dev/null || true
Xvfb "$DISPLAY_NUMBER" -screen 0 "$SCREEN_SIZE" -ac -nolisten tcp &
XVFB_PID=$!

i=0
while [ ! -S "/tmp/.X11-unix/X${DISPLAY_NUMBER#:}" ]; do
  i=$((i + 1))
  [ "$i" -lt 100 ] || { echo "Xvfb startup timed out" >&2; exit 1; }
  sleep 0.1
done

if command -v openbox >/dev/null 2>&1; then
  openbox-session >/tmp/recorder-openbox.log 2>&1 &
  WM_PID=$!
fi

x11vnc -display "$DISPLAY_NUMBER" -rfbport "$VNC_PORT" -forever -shared -nopw \
  -noxdamage -repeat -xkb -quiet >/tmp/recorder-x11vnc.log 2>&1 &
VNC_PID=$!

NOVNC_WEB="${RECORDER_NOVNC_WEB_ROOT:-/usr/share/novnc}"
[ -d "$NOVNC_WEB" ] || { echo "noVNC web root not found: $NOVNC_WEB" >&2; exit 1; }
websockify --web="$NOVNC_WEB" "$WEB_PORT" "127.0.0.1:$VNC_PORT" &
WEBSOCKIFY_PID=$!

wait "$WEBSOCKIFY_PID"
