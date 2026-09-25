def test_lidl_creds_from_settings_then_env(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "a.db"))
    dbmod.init_db()
    monkeypatch.setenv("LIDL_USER", "envuser")
    monkeypatch.delenv("LIDL_PASS", raising=False)
    import fetch_lidl as fl
    assert fl.get_lidl_creds() == ("envuser", "")


def test_lidl_creds_db_wins(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "a2.db"))
    dbmod.init_db()
    monkeypatch.setenv("LIDL_USER", "envuser")
    monkeypatch.setenv("LIDL_PASS", "envpass")
    import settings_store as ss
    ss.set("LIDL_USER", "dbuser")
    import fetch_lidl as fl
    assert fl.get_lidl_creds() == ("dbuser", "envpass")


def test_paste_route(tmp_path, monkeypatch):
    import db as dbmod, os
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    src = os.path.join(os.path.dirname(appmod.__file__), "tests", "data_paste.txt")
    assert os.path.exists(src), "fixture missing"
    with open(src, encoding="utf-8") as f:
        text = f.read()
    r = c.post("/fetch/paste", data={"text": text, "tid": "test-1"}, follow_redirects=True)
    assert r.status_code == 200
