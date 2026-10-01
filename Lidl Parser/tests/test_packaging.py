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


import sys as _sys
import pathlib as _pl
_PKG = _pl.Path(__file__).resolve().parent.parent.parent / "packaging" / "windows"
if str(_PKG) not in _sys.path:
    _sys.path.insert(0, str(_PKG))
if str(_PKG) not in _sys.path:
    _sys.path.insert(0, str(_PKG))
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


def test_launcher_build_env_points_at_base_dirs(tmp_path):
    env = _launcher.build_env("s3cret", base=tmp_path)
    assert env["LIDL_DB"] == str(tmp_path / "data" / "lidl.db")
    assert env["RECEIPT_DIR"] == str(tmp_path / "receipts")


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


def test_serve_entrypoint_content():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "packaging" / "windows" / "serve.py").read_text(encoding="utf-8")
    assert "waitress" in src and "serve" in src
    assert "127.0.0.1" in src


def test_serve_resolves_staged_zip_layout(tmp_path):
    import pathlib
    import shutil
    import subprocess
    import sys
    real = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "packaging" / "windows" / "serve.py")
    root = tmp_path / "ziproot"
    (root / "Lidl Parser").mkdir(parents=True)
    shutil.copy(real, root / "serve.py")
    out = subprocess.run([sys.executable, str(root / "serve.py"), "--check"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(root / "Lidl Parser")


def test_app_secret_key_from_env():
    import pathlib
    import subprocess
    import sys
    here = pathlib.Path(__file__).resolve().parent.parent
    code = "import app; print(app.app.secret_key)"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(here),
                         capture_output=True, text=True, timeout=120,
                         env={**__import__("os").environ, "SECRET_KEY": "zip-secret-123",
                              "LIDL_DB": ":memory:"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "zip-secret-123"
