import time


def _wait_idle(appmod, timeout=10):
    for _ in range(100):
        if not appmod._FETCH["running"]:
            return True
        time.sleep(0.1)
    return False


def _client(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "login.db"))
    dbmod.init_db()
    import app as appmod
    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync", lambda **kw: 3)
    appmod.app.testing = True
    return appmod.app.test_client(), appmod


def test_login_needed_without_creds_points_to_settings(tmp_path, monkeypatch):
    """No creds saved -> error must say Settings, never a success result."""
    monkeypatch.delenv("LIDL_USER", raising=False)
    monkeypatch.delenv("LIDL_PASS", raising=False)
    c, appmod = _client(tmp_path, monkeypatch)
    assert c.post("/fetch/run").get_json()["started"] is True
    assert _wait_idle(appmod)
    s = c.get("/api/fetch_status").get_json()
    assert s["running"] is False
    assert s["result"] is None
    assert "settings" in s["error"].lower()


def test_login_needed_with_creds_points_to_paste(tmp_path, monkeypatch):
    """Creds saved but auto-login blocked -> error must say paste fallback."""
    monkeypatch.setenv("LIDL_USER", "u")
    monkeypatch.setenv("LIDL_PASS", "p")
    c, appmod = _client(tmp_path, monkeypatch)
    assert c.post("/fetch/run").get_json()["started"] is True
    assert _wait_idle(appmod)
    s = c.get("/api/fetch_status").get_json()
    assert s["running"] is False
    assert s["result"] is None
    assert "paste" in s["error"].lower()
