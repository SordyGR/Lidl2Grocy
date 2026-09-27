#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"

# Keep the interactive Chromium display inside the container. Docker publishes
# the noVNC port on host loopback only; remote users can reach it through SSH.
Xvfb "$DISPLAY" -screen 0 1366x900x24 -nolisten tcp &
sleep 1
x11vnc -display "$DISPLAY" -forever -shared -localhost -rfbport 5900 -nopw &
websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 &
sleep 1

python migrate.py
exec gunicorn -w 1 --threads 4 -b 0.0.0.0:5090 app:app
