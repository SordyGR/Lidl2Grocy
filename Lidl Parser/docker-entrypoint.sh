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

# VNC password: VNC_PASSWORD env wins, else a generated one persisted in
# data/.vnc-pass (chmod 600, survives rebuilds via the ./data volume).
# The noVNC viewer prompts for it. First boot prints where to find it;
# `docker compose logs` shows this line — LAN-only exposure is assumed.
PASS_FILE="/app/data/.vnc-pass"
if [ -n "${VNC_PASSWORD:-}" ]; then
    x11vnc -storepasswd "$VNC_PASSWORD" "$PASS_FILE" >/dev/null 2>&1
    chmod 600 "$PASS_FILE"
elif [ ! -s "$PASS_FILE" ]; then
    head -c 18 /dev/urandom | base64 | tr -d '\n' > "$PASS_FILE"
    chmod 600 "$PASS_FILE"
    echo "Generated VNC password stored in data/.vnc-pass (see docker host ./data/.vnc-pass)" >&2
fi

fluxbox -display "$DISPLAY" >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -localhost -rfbport 5900 -rfbauth "$PASS_FILE" -noxdamage &
websockify --verbose 6080 127.0.0.1:5900 &

python migrate.py
gunicorn -w 1 --threads 4 -b 127.0.0.1:5091 app:app &
exec nginx -g 'daemon off;'
