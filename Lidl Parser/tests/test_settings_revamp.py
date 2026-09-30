import db as dbmod


def _client(tmp_path, monkeypatch, name="st.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed_users():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()[0]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    iid = conn.execute("SELECT id FROM items WHERE receipt_id=?", (rid,)).fetchone()[0]
    conn.commit()
    conn.close()
    return rid, iid


def _settings_body(c, v2=False):
    if v2:
        c.set_cookie("ui", "v2")
    try:
        return c.get("/settings").get_data(as_text=True)
    finally:
        if v2:
            c.delete_cookie("ui")


def test_api_key_hidden_and_empty_keeps(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import settings_store as ss
    ss.set("GROCY_URL", "https://g.example/api")
    ss.set("GROCY_API_KEY", "secret123")
    for v2 in (False, True):
        body = _settings_body(c, v2)
        assert 'type="password"' in body
        assert "secret123" not in body
    c.post("/settings", data={"form": "grocy", "GROCY_URL": "https://g.example/api",
                              "GROCY_API_KEY": ""}, follow_redirects=True)
    assert ss.get("GROCY_API_KEY") == "secret123"
    c.post("/settings", data={"form": "grocy", "GROCY_URL": "https://g.example/api",
                              "GROCY_API_KEY": "newkey"}, follow_redirects=True)
    assert ss.get("GROCY_API_KEY") == "newkey"


def test_expandable_sections(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st2.db")
    for v2 in (False, True):
        body = _settings_body(c, v2)
        assert "<details" in body
        for title in ("Excluded from charts", "Units", "Categories"):
            assert title in body


def test_rename_user(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st3.db")
    rid, iid = _seed_users()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)", (iid, 1))
    conn.commit()
    conn.close()
    c.post("/settings/users/rename", data={"id": "1", "name": "Alicia"},
           follow_redirects=True)
    conn = dbmod.get_db()
    assert conn.execute("SELECT name FROM users WHERE id=1").fetchone()[0] == "Alicia"
    assert conn.execute("SELECT COUNT(*) FROM item_splits WHERE user_id=1").fetchone()[0] == 1
    conn.close()


def test_vacation_hides_from_splits_keeps_history(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st4.db")
    rid, iid = _seed_users()
    c.get(f"/receipts/{rid}")  # seeds equal shares
    c.post("/settings/users/vacation", data={"id": "2", "active": "0"},
           follow_redirects=True)
    body = c.get(f"/receipts/{rid}").get_data(as_text=True)
    assert "share_1" in body and "share_2" not in body
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM item_splits WHERE user_id=2").fetchone()[0] == 1
    conn.close()
    c.post("/settings/users/vacation", data={"id": "2", "active": "1"},
           follow_redirects=True)
    assert "share_2" in c.get(f"/receipts/{rid}").get_data(as_text=True)


def test_delete_user_blocked_when_tied(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st5.db")
    rid, iid = _seed_users()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)", (iid, 1))
    conn.commit()
    conn.close()
    r = c.post("/settings/users/delete", data={"id": "1"}, follow_redirects=True)
    assert "tied" in r.get_data(as_text=True).lower() or "linked" in r.get_data(as_text=True).lower()
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id=1").fetchone()[0] == 1
    conn.execute("DELETE FROM item_splits WHERE user_id=1")
    conn.commit()
    conn.close()
    c.post("/settings/users/delete", data={"id": "1"}, follow_redirects=True)
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM users WHERE id=1").fetchone()[0] == 0
    conn.close()


def test_card_reassign(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st6.db")
    _seed_users()
    c.post("/settings/cards", data={"last4": "1234", "user_id": "1"},
           follow_redirects=True)
    c.post("/settings/cards", data={"last4": "1234", "user_id": "2"},
           follow_redirects=True)
    body = c.get("/settings").get_data(as_text=True)
    assert "1234" in body and "Bob" in body


def test_theme_and_ui_redirect_back(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "st7.db")
    r = c.get("/theme/dark?next=/prices")
    assert r.status_code == 302 and r.headers["Location"].endswith("/prices")
    r = c.get("/theme/dark?next=https://evil.example/")
    assert r.headers["Location"] == "/"
    r = c.get("/ui/v2?next=/receipts")
    assert r.status_code == 302 and r.headers["Location"].endswith("/receipts")
