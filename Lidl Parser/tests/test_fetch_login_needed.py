import time


def _wait_idle(appmod, timeout=10):
    for _ in range(100):
        if not appmod._FETCH["running"]:
            return True
        time.sleep(0.1)
    return False


def test_fetch_login_needed_is_error_not_result(tmp_path, monkeypatch):
    """cmd_sync returns exit code 3 on LOGIN NEEDED — must surface as
    fetch_status.error (no auto-reload), never as a success result."""
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "login.db"))
    dbmod.init_db()
    import app as appmod
    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync", lambda **kw: 3)
    appmod.app.testing = True
    c = appmod.app.test_client()
    assert c.post("/fetch/run").get_json()["started"] is True
    assert _wait_idle(appmod)
    s = c.get("/api/fetch_status").get_json()
    assert s["running"] is False
    assert s["result"] is None
    assert s["error"] and "login" in s["error"].lower()
