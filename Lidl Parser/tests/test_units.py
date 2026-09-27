import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_units_refresh_upserts(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import grocy as g
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {
        "locations": [], "quantity_units": [{"id": 7, "name": "Piece"}], "cached_at": 0})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    r = c.post("/settings/units-refresh", follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id, source FROM units WHERE name='Piece'").fetchone()
    conn.close()
    assert row["grocy_id"] == 7 and row["source"] == "grocy"


def test_units_refresh_offline_keeps_cache(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import grocy as g

    def _boom(*a):
        raise g.GrocyApiError(500, "down") if hasattr(g, "GrocyApiError") else ConnectionError("down")

    monkeypatch.setattr(g, "fetch_lookups", _boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    before = dbmod.get_db().execute("SELECT COUNT(*) FROM units").fetchone()[0]
    r = c.post("/settings/units-refresh", follow_redirects=True)
    assert r.status_code == 200
    after = dbmod.get_db().execute("SELECT COUNT(*) FROM units").fetchone()[0]
    assert after == before and "Τεμάχιο" in r.get_data(as_text=True)


def test_settings_shows_units(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    body = c.get("/settings").get_data(as_text=True)
    assert "Units" in body and "Τεμάχιο" in body
