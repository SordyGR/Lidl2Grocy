def test_settings_roundtrip_and_secret_redaction(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/settings", data={"GROCY_URL": "https://g.example/api",
                                  "GROCY_API_KEY": "k", "LIDL_USER": "u",
                                  "LIDL_PASS": "p"}, follow_redirects=True)
    assert r.status_code == 200
    body = c.get("/settings").get_data(as_text=True)
    assert "https://g.example/api" in body
    assert ">p<" not in body and 'value="p"' not in body


def test_grocy_url_without_api_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s2.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings", data={"GROCY_URL": "https://g.example",
                              "GROCY_API_KEY": "k"}, follow_redirects=True)
    import settings_store as ss
    assert ss.get("GROCY_URL") == ""


def test_settings_shows_extension_token_and_regenerates(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s4.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    body = c.get("/settings").get_data(as_text=True)
    assert "Extension" in body
    assert "LIDL_PASS" not in body and "lidl-profile" not in body
    old = appmod._ext_token()
    assert old in body
    r = c.post("/settings/ext-regenerate", follow_redirects=True)
    assert r.status_code == 200
    assert appmod._ext_token() != old
