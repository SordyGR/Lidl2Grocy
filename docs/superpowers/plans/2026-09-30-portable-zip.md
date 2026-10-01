# Portable Windows zip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `Lidl2Grocy-<VERSION>-win64.zip` that runs on Python-less Windows via double-click.

**Architecture:** `packaging/windows/` holds a `Start.bat`, a stdlib-only
`launcher.py` (dirs, secret, migrate, serve, open browser), and a `serve.py`
waitress entrypoint; CI on tags bundles CPython embeddable + preinstalled
wheels and attaches the zip to the GitHub release.

**Tech Stack:** Windows batch, Python stdlib (launcher), waitress (serve),
CPython embeddable 3.13, GitHub Actions Windows runner.

**Spec:** `docs/superpowers/specs/2026-09-30-distribution-design.md` (section B)

## Global Constraints

- Launcher is **stdlib-only** (must run on bare embeddable Python).
- No `app.py`/`db.py` behavior changes; config flows via env (`SECRET_KEY`).
- App dir name contains a space (`Lidl Parser`) — use `pathlib`/arg lists,
  never shell-string paths.
- Zip layout root: `Start.bat`, `launcher.py`, `serve.py`,
  `requirements-standalone.txt`, `freeze.txt`, `README.txt`, `python/`,
  `Lidl Parser/`, (created at runtime: `data/`, `receipts/`).
- Serve binds `127.0.0.1`, port picked from 5000–5009.
- Git commit/tag/push is done by the human in their own terminal, never by
  the agent. Task steps end at verification.
- Shell is Windows PowerShell: no `tail`, no `&&` chaining.

## Review Focus

- `waitress` missing in dev when exercising serve paths → launcher spawns it
  as a child (ImportError surfaces as a clear child-process failure, never an
  import-time crash of the launcher); pinned by Task 3 test asserting the
  spawn uses `sys.executable` + `serve.py`.
- SECRET_FILE readable by other local users → accepted and documented in
  `README.txt` (physical-access machine assumed); no fake ACL code.
- Browser-open race (page opened before serve ready) → launcher polls `/`
  up to 15 s before opening; pinned by Task 3 test with a stdlib test server.
- requirements drift vs Docker (pandas major bump breaks parser) →
  `freeze.txt` snapshot per release; minimum pins in
  `requirements-standalone.txt` stay aligned with root `requirements.txt`.
- SmartScreen/antivirus warnings on first run → `README.txt` reassurance
  note; not solvable in code.

---

### Task 1: Static packaging files

**Files:**
- Create: `packaging/windows/requirements-standalone.txt`
- Create: `packaging/windows/README.txt`
- Create: `packaging/windows/Start.bat`
- Test: `Lidl Parser/tests/test_packaging.py`

**Interfaces:**
- Consumes: root `requirements.txt` versions (mirror minimums).
- Produces: file paths the launcher/CI tasks rely on.

- [ ] **Step 1: Write the failing test**

```python
def test_packaging_static_files():
    import pathlib
    base = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "packaging" / "windows")
    reqs = (base / "requirements-standalone.txt").read_text(encoding="utf-8").lower()
    for pkg in ("flask", "pandas", "rapidfuzz", "openpyxl", "requests",
                "cryptography", "waitress"):
        assert pkg in reqs
    assert "gunicorn" not in reqs and "pytest" not in reqs
    bat = (base / "Start.bat").read_text(encoding="utf-8")
    assert "launcher.py" in bat and "pause" in bat
    readme = (base / "README.txt").read_text(encoding="utf-8")
    assert "Start.bat" in readme and "5000" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaging.py::test_packaging_static_files -v`
(CWD: `Lidl Parser/`)
Expected: FAIL (FileNotFoundError)

- [ ] **Step 3: Write minimal implementation**

`packaging/windows/requirements-standalone.txt`:
```
Flask>=3.0
pandas>=2.0
rapidfuzz>=3.0
openpyxl>=3.1
requests>=2.0
cryptography>=41.0
waitress>=2.1
```

`packaging/windows/Start.bat`:
```bat
@echo off
set HERE=%~dp0
"%HERE%python\python.exe" "%HERE%launcher.py" %*
if errorlevel 1 pause
```

`packaging/windows/README.txt`:
```
Lidl2Grocy portable (Windows, no install needed)

1. Unzip this folder anywhere (e.g. Documents).
2. Double-click Start.bat.
3. Your browser opens on the app (http://127.0.0.1:5000, or the next
   free port 5001-5009 if 5000 is taken).

Your data stays in data\ and receipts\ next to Start.bat - back those
folders up to keep everything. An antivirus warning on first run is
normal for unsigned software; the code is the public Lidl2Grocy repo.

Lidl auto-sync needs the Firefox extension (temporary sideload, see the
wiki); without it, paste receipts on the dashboard or upload .txt files.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_packaging.py -v`
Expected: PASS

### Task 2: launcher.py pure functions + tests

**Files:**
- Create: `packaging/windows/launcher.py`
- Test: `Lidl Parser/tests/test_packaging.py` (append; import via
  `sys.path.insert(0, <packaging/windows>)` guarded by importorskip-style
  try/except that fails loudly if missing)

**Interfaces:**
- Consumes: nothing.
- Produces: `ensure_dirs(base) -> (data_dir, receipts_dir)`;
  `load_or_create_secret(path) -> str` (32-byte urlsafe, persisted);
  `pick_port(host, start, tries) -> int` (socket-bind probe, RuntimeError
  when exhausted); `wait_for_http(url, timeout_s) -> bool` (urllib poll);
  `build_env(secret) -> dict` (os.environ + SECRET_KEY).

- [ ] **Step 1: Write the failing tests** (append)

```python
import sys as _sys
import pathlib as _pl
_PKG = _pl.Path(__file__).resolve().parent.parent.parent / "packaging" / "windows"
if str(_PKG) not in _sys.path:
    _sys.path.insert(0, str(_PKG))
import launcher as _launcher


def test_launcher_secret_persists(tmp_path):
    p = tmp_path / ".secret"
    first = _launcher.load_or_create_secret(p)
    assert len(first) >= 32
    assert _launcher.load_or_create_secret(p) == first


def test_launcher_dirs_and_port(tmp_path):
    data, receipts = _launcher.ensure_dirs(tmp_path)
    assert data.is_dir() and receipts.is_dir()
    port = _launcher.pick_port("127.0.0.1", 5000, 10)
    assert 5000 <= port <= 5009
    try:
        _launcher.pick_port("127.0.0.1", 5000, 0)
    except RuntimeError:
        pass
    else:
        raise AssertionError("exhausted range must raise")


def test_launcher_build_env():
    env = _launcher.build_env("s3cret")
    assert env["SECRET_KEY"] == "s3cret"


def test_launcher_waits_for_http():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import launcher as L

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert L.wait_for_http(f"http://127.0.0.1:{srv.server_port}/", 5) is True
    finally:
        srv.shutdown()
    assert L.wait_for_http("http://127.0.0.1:1/", 1) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_packaging.py -v`
Expected: FAIL (ImportError: No module named 'launcher')

- [ ] **Step 3: Write minimal implementation**

`packaging/windows/launcher.py` (stdlib only):
```python
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


def build_env(secret):
    return {**os.environ, "SECRET_KEY": secret}


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_packaging.py -v`
Expected: PASS

### Task 3: serve.py entrypoint + spawn test

**Files:**
- Create: `packaging/windows/serve.py`
- Test: `Lidl Parser/tests/test_packaging.py` (append)

**Interfaces:**
- Consumes: `waitress` (bundle/runtime only), `Lidl Parser/app.py:app`.
- Produces: `python serve.py <port>` serving the app on 127.0.0.1.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_serve_entrypoint_content():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "packaging" / "windows" / "serve.py").read_text(encoding="utf-8")
    assert "waitress" in src and "serve" in src
    assert "127.0.0.1" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaging.py::test_serve_entrypoint_content -v`
Expected: FAIL (FileNotFoundError)

- [ ] **Step 3: Write minimal implementation**

`packaging/windows/serve.py`:
```python
"""Waitress entrypoint for the portable zip (needs waitress installed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "Lidl Parser"))
from waitress import serve  # noqa: E402
import app as appmod  # noqa: E402

if __name__ == "__main__":
    serve(appmod.app, host="127.0.0.1", port=int(sys.argv[1]), threads=4)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_packaging.py -v`
Expected: PASS

### Task 4: Release job (Windows zip on tags)

**Files:**
- Modify: `.github/workflows/docker-publish.yml` (add `release` job)
- Test: `Lidl Parser/tests/test_distrib_docker.py` (append)

**Interfaces:**
- Consumes: root `VERSION`, `packaging/windows/*`, `Lidl Parser/`
  (app code + extension + requirements).
- Produces: `Lidl2Grocy-<VERSION>-win64.zip` + `freeze.txt` on the GitHub
  release for tag `v<VERSION>`.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_release_job_content():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent.parent
          / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    assert "windows-latest" in wf
    assert "requirements-standalone.txt" in wf
    assert "freeze.txt" in wf
    assert "Lidl2Grocy-" in wf and "win64.zip" in wf
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_distrib_docker.py::test_release_job_content -v`
Expected: FAIL (assertion on windows-latest)

- [ ] **Step 3: Write minimal implementation** (append job to the workflow
  from the prebuilt-image plan; `PYTHON_VERSION` pinned, verify the URL
  returns 200 — use newest 3.13.x if superseded):

```yaml
  release:
    runs-on: windows-latest
    env:
      PYTHON_VERSION: "3.13.7"
    steps:
      - uses: actions/checkout@v4
      - name: Tag matches VERSION
        shell: pwsh
        run: |
          $v = (Get-Content VERSION -Raw).Trim()
          if ("v$v" -ne $env:GITHUB_REF_NAME) { throw "tag $env:GITHUB_REF_NAME != v$v" }
      - name: Download embeddable Python
        shell: pwsh
        run: |
          $url = "https://www.python.org/ftp/python/$env:PYTHON_VERSION/python-$env:PYTHON_VERSION-embed-amd64.zip"
          curl.exe -L -o pyembed.zip $url
          mkdir stage\python
          Expand-Archive pyembed.zip stage\python
          "import site" | Out-File -Append stage\python\python313._pth
      - name: Install get-pip and wheels
        shell: pwsh
        run: |
          curl.exe -L -o get-pip.py https://bootstrap.pypa.io/get-pip.py
          .\stage\python\python.exe get-pip.py
          .\stage\python\python.exe -m pip install -r packaging/windows/requirements-standalone.txt
          .\stage\python\python.exe -m pip freeze | Out-File -Encoding utf8 stage\freeze.txt
      - name: Assemble zip
        shell: pwsh
        run: |
          $v = (Get-Content VERSION -Raw).Trim()
          mkdir stage\"Lidl Parser"
          Copy-Item "Lidl Parser/app.py","Lidl Parser/db.py","Lidl Parser/receipt_parser.py","Lidl Parser/fetch_lib.py","Lidl Parser/fetch_lidl.py","Lidl Parser/grocy.py","Lidl Parser/migrate.py","Lidl Parser/settings_store.py","Lidl Parser/secret_store.py" stage\"Lidl Parser"
          Copy-Item -Recurse "Lidl Parser/templates","Lidl Parser/static","Lidl Parser/extension" stage\"Lidl Parser"
          Copy-Item packaging/windows/Start.bat,packaging/windows/launcher.py,packaging/windows/serve.py,packaging/windows/requirements-standalone.txt,packaging/windows/README.txt stage
          $v = (Get-Content VERSION -Raw).Trim()
          Compress-Archive -Path stage\* -DestinationPath "Lidl2Grocy-$v-win64.zip"
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            Lidl2Grocy-*-win64.zip
            stage/freeze.txt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_distrib_docker.py tests/test_packaging.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest -q` (CWD: `Lidl Parser/`)
Expected: all green (185 + new tests).
