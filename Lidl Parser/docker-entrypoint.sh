#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"

# Start a complete virtual desktop so headed Chromium can be viewed remotely.
# NOTE: there is no process supervision here — if Xvfb/x11vnc/websockify or
# gunicorn die, nginx keeps serving with no signal. For a single-user LAN
# app this is acceptable; add a supervisor (or HEALTHCHECK) if that changes.
Xvfb "$DISPLAY" -screen 0 1366x900x24 -nolisten tcp &
ready=0
for _ in $(seq 1 30); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "Xvfb did not become ready on $DISPLAY" >&2
    exit 1
fi

# No VNC password by design (LAN-only). The noVNC viewer connects directly.
fluxbox -display "$DISPLAY" >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -localhost -rfbport 5900 -nopw -noxdamage &
websockify --verbose 6080 127.0.0.1:5900 &

python migrate.py
gunicorn -w 1 --threads 4 -b 127.0.0.1:5091 app:app &
exec nginx -g 'daemon off;'
