import time


def _wait_idle(appmod, timeout=10):
    for _ in range(100):
        if not appmod._FETCH["running"]:
            return True
        time.sleep(0.1)
    return False


def test_fetch_run_single_flight(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "f.db"))
    dbmod.init_db()
    import app as appmod
    calls = []
    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync",
                        lambda **kw: calls.append(kw) or 0)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r1 = c.post("/fetch/run")
    assert r1.get_json()["started"] is True
    assert _wait_idle(appmod)
    assert calls == [{"latest_only": True}]
    r2 = c.post("/fetch/run")
    assert r2.get_json()["started"] is True
    assert _wait_idle(appmod)
    assert len(calls) == 2
    s = c.get("/api/fetch_status").get_json()
    assert s["running"] is False and s["result"] == 0


def test_fetch_run_overlapping_rejected(tmp_path, monkeypatch):
    import threading
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "f2.db"))
    dbmod.init_db()
    import app as appmod
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_sync(**kw):
        calls.append(kw)
        entered.set()
        assert release.wait(timeout=10)
        return 0

    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync", blocked_sync)
    appmod.app.testing = True
    c = appmod.app.test_client()
    results = {}

    def first():
        results["r1"] = c.post("/fetch/run").get_json()

    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(timeout=10)
    r2 = c.post("/fetch/run")
    assert r2.get_json()["started"] is False
    release.set()
    t.join(timeout=10)
    assert results["r1"]["started"] is True
    assert _wait_idle(appmod)
    assert calls == [{"latest_only": True}]
