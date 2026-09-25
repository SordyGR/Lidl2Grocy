def test_resolution_prefers_db_over_env(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    monkeypatch.setenv("GROCY_URL", "https://env.example/api")
    import settings_store as ss
    assert ss.get("GROCY_URL") == "https://env.example/api"
    ss.set("GROCY_URL", "https://db.example/api")
    assert ss.get("GROCY_URL") == "https://db.example/api"


def test_unknown_key_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s2.db"))
    dbmod.init_db()
    import settings_store as ss
    ss.set("SECRET_KEY", "x")
    assert ss.get("SECRET_KEY") == ""
