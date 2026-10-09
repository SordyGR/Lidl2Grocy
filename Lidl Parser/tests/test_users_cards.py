def test_user_crud_and_card_link(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings/users", data={"name": "Alice"}, follow_redirects=True)
    c.post("/settings/users", data={"name": "Bob"}, follow_redirects=True)
    c.post("/settings/cards", data={"last4": "1234", "user_id": "1"},
           follow_redirects=True)
    body = c.get("/settings").get_data(as_text=True)
    assert "Alice" in body and "1234" in body
    conn = dbmod.get_db()
    link = conn.execute("SELECT user_id FROM card_map WHERE last4='1234'").fetchone()
    conn.close()
    assert link["user_id"] == 1


def test_bad_last4_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u2.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings/users", data={"name": "Alice"}, follow_redirects=True)
    r = c.post("/settings/cards", data={"last4": "93", "user_id": "1"},
               follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM card_map").fetchone()[0]
    conn.close()
    assert n == 0
