"""Portable-zip launcher: dirs, secret, migrate, serve, open browser."""
import os
import secrets
import socket
import subprocess
import sys
import urllib.request
import webbrowser
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP_DIR = BASE / "Lidl Parser"
HOST, START_PORT, PORT_TRIES = "127.0.0.1", 5000, 10


def ensure_dirs(base=BASE):
    data, receipts = Path(base) / "data", Path(base) / "receipts"
    data.mkdir(parents=True, exist_ok=True)
    receipts.mkdir(parents=True, exist_ok=True)
    return data, receipts


def load_or_create_secret(path=None):
    path = Path(path) if path is not None else BASE / "data" / ".secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(32)
    path.write_text(value, encoding="utf-8")
    return value


def pick_port(host=HOST, start=START_PORT, tries=PORT_TRIES):
    for port in range(start, start + max(tries, 0)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {start}-{start + tries - 1}")


def build_env(secret, base=BASE):
    base = Path(base)
    return {**os.environ,
            "SECRET_KEY": secret,
            "LIDL_DB": str(base / "data" / "lidl.db"),
            "RECEIPT_DIR": str(base / "receipts")}


def wait_for_http(url, timeout_s=15):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    data, _receipts = ensure_dirs()
    secret = load_or_create_secret(data / ".secret")
    env = build_env(secret)
    subprocess.run([sys.executable, "migrate.py"], cwd=str(APP_DIR),
                   env=env, check=True)
    port = pick_port()
    proc = subprocess.Popen(
        [sys.executable, str(BASE / "serve.py"), str(port)],
        cwd=str(APP_DIR), env=env)
    try:
        if wait_for_http(f"http://{HOST}:{port}/"):
            webbrowser.open(f"http://{HOST}:{port}/")
            proc.wait()
        else:
            raise RuntimeError("server did not answer in time")
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
