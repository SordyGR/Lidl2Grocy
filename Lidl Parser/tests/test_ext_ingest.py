import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "ext.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_token_autogenerates_and_persists(tmp_path, monkeypatch):
    import app as appmod
    _client(tmp_path, monkeypatch)
    t1 = appmod._ext_token()
    assert len(t1) >= 32
    assert appmod._ext_token() == t1  # second call reads DB, no regen


def test_auth_gate(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    assert c.get("/api/ext/status").status_code == 401
    assert c.get("/api/ext/status",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/ext/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert tok not in r.get_data(as_text=True)
