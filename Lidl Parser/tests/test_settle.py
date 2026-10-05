def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    ids = {r["name"]: r["id"] for r in
           conn.execute("SELECT id, name FROM users").fetchall()}
    conn.execute("INSERT INTO card_map (last4, user_id) VALUES ('1111', ?)",
                 (ids["Alice"],))
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4, item_count, total) "
                 "VALUES ('r1.txt','2026-09-01','1111',1,10.0)")
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4, item_count, total) "
                 "VALUES ('r2.txt','2026-09-10',NULL,1,20.0)")
    r1 = conn.execute("SELECT id FROM receipts WHERE filename='r1.txt'").fetchone()["id"]
    r2 = conn.execute("SELECT id FROM receipts WHERE filename='r2.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (r1, "MILK", "Milk", 10.0))
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (r2, "BREAD", "Bread", 20.0))
    i1 = conn.execute("SELECT id FROM items WHERE receipt_id=?", (r1,)).fetchone()["id"]
    i2 = conn.execute("SELECT id FROM items WHERE receipt_id=?", (r2,)).fetchone()["id"]
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                 (i1, ids["Alice"], 0.5))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                 (i1, ids["Bob"], 0.5))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                 (i2, ids["Bob"], 1.0))
    conn.commit()
    conn.close()
    return r1, r2


def _client(tmp_path, monkeypatch, name):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    r1, r2 = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client(), r1, r2


def test_balances_cover_unsettled_only(tmp_path, monkeypatch):
    import db as dbmod
    c, r1, r2 = _client(tmp_path, monkeypatch, "st1.db")
    body = c.get("/").get_data(as_text=True)
    assert "Alice" in body and "Bob" in body
    c.post("/receipts/settle", data={"from": "2026-09-01", "to": "2026-09-01"},
           follow_redirects=True)
    conn = dbmod.get_db()
    s1 = conn.execute("SELECT settled FROM receipts WHERE id=?", (r1,)).fetchone()["settled"]
    s2 = conn.execute("SELECT settled FROM receipts WHERE id=?", (r2,)).fetchone()["settled"]
    conn.close()
    assert s1 == 1 and s2 == 0


def test_settle_with_exclusion_leaves_complex_receipt(tmp_path, monkeypatch):
    import db as dbmod
    c, r1, r2 = _client(tmp_path, monkeypatch, "st2.db")
    # settle by explicit id list: only r1, r2 (complex) stays open
    c.post("/receipts/settle", data={"receipt_ids": str(r1)},
           follow_redirects=True)
    conn = dbmod.get_db()
    s1 = conn.execute("SELECT settled FROM receipts WHERE id=?", (r1,)).fetchone()["settled"]
    s2 = conn.execute("SELECT settled FROM receipts WHERE id=?", (r2,)).fetchone()["settled"]
    conn.close()
    assert s1 == 1 and s2 == 0


def test_receipts_list_shows_settled_badge(tmp_path, monkeypatch):
    c, r1, r2 = _client(tmp_path, monkeypatch, "st3.db")
    c.post(f"/receipts/{r1}/settled", data={"settled": "1"},
           follow_redirects=True)
    for cookie in (None, "v2"):
        if cookie:
            c.set_cookie("ui", "v2")
        try:
            body = c.get("/receipts").get_data(as_text=True)
        finally:
            if cookie:
                c.delete_cookie("ui")
        assert "Settled" in body and "Unsettled" in body


def test_per_receipt_settle_toggle_reopens(tmp_path, monkeypatch):
    import db as dbmod
    c, r1, r2 = _client(tmp_path, monkeypatch, "st4.db")
    c.post(f"/receipts/{r1}/settled", data={"settled": "1"},
           follow_redirects=True)
    c.post(f"/receipts/{r1}/settled", data={"settled": "0"},
           follow_redirects=True)
    conn = dbmod.get_db()
    s1 = conn.execute("SELECT settled FROM receipts WHERE id=?", (r1,)).fetchone()["settled"]
    conn.close()
    assert s1 == 0


def test_settled_toggle_redirect_targets_row(tmp_path, monkeypatch):
    c, r1, r2 = _client(tmp_path, monkeypatch, "st5.db")
    r = c.post(f"/receipts/{r1}/settled", data={"settled": "1"},
               follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert r.headers["Location"].endswith(f"/receipts#r-{r1}")


def test_dashboard_settle_redirect_targets_balances(tmp_path, monkeypatch):
    c, r1, r2 = _client(tmp_path, monkeypatch, "st6.db")
    r = c.post("/receipts/settle", data={"from": "2026-09-01", "to": "2026-09-30"},
               follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert r.headers["Location"].endswith("/#balances")


def _seed_unopened(dbmod, filename, card_last4, total):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4, item_count, total) "
                 "VALUES (?, '2026-09-15', ?, 1, ?)", (filename, card_last4, total))
    rid = conn.execute("SELECT id FROM receipts WHERE filename=?", (filename,)).fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", total))
    conn.commit()
    conn.close()
    return rid


def test_unopened_receipt_counts_in_balances(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "st7.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    alice = conn.execute("SELECT id FROM users WHERE name='Alice'").fetchone()["id"]
    conn.execute("INSERT INTO card_map (last4, user_id) VALUES ('1111', ?)", (alice,))
    conn.commit()
    conn.close()
    _seed_unopened(dbmod, "u1.txt", "1111", 10.0)
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    # Alice paid 10, each owes half → Bob owes Alice €5.00
    assert "Bob owes Alice" in body


def test_cardless_receipt_nets_zero(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "st8.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.commit()
    conn.close()
    _seed_unopened(dbmod, "u2.txt", None, 10.0)
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    assert "Balances" in body
    assert " owes " not in body
    # cash counts as paid by both halves AND owed by both halves → visible 5.00s
    assert body.count("<td>5.00</td>") >= 2
