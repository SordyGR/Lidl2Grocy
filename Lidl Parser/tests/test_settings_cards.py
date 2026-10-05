def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    alice = conn.execute("SELECT id FROM users WHERE name='Alice'").fetchone()["id"]
    conn.execute("INSERT INTO card_map (last4, user_id) VALUES ('1111', ?)", (alice,))
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4) VALUES ('a.txt','2026-09-01','1111')")
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4) VALUES ('b.txt','2026-09-02','2222')")
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4) VALUES ('c.txt','2026-09-03',NULL)")
    conn.commit()
    conn.close()


def test_settings_lists_used_but_unmapped_cards(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "cards.db"))
    dbmod.init_db()
    _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/settings").get_data(as_text=True)
    assert "1111" in body
    assert "2222" in body  # used on a receipt but never linked — must still be listed
    assert "unassigned" in body.lower()


def test_settings_card_link_assigns_used_card(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "cards2.db"))
    dbmod.init_db()
    _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    bob = dbmod.get_db().execute("SELECT id FROM users WHERE name='Bob'").fetchone()["id"]
    c.post("/settings/cards", data={"last4": "2222", "user_id": str(bob)},
           follow_redirects=True)
    conn = dbmod.get_db()
    row = conn.execute("SELECT user_id FROM card_map WHERE last4='2222'").fetchone()
    conn.close()
    assert row is not None and row["user_id"] == bob


def test_settings_saves_land_on_section(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "cards3.db"))
    dbmod.init_db()
    _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    alice = dbmod.get_db().execute("SELECT id FROM users WHERE name='Alice'").fetchone()["id"]
    r = c.post("/settings/cards", data={"last4": "2222", "user_id": str(alice)},
               follow_redirects=False)
    assert r.headers["Location"].endswith("/settings#cards")
    r = c.post("/settings/users", data={"name": "Zed"},
               follow_redirects=False)
    assert r.headers["Location"].endswith("/settings#users")
