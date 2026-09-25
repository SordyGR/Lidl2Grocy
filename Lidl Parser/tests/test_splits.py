def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    iid = conn.execute("SELECT id FROM items WHERE receipt_id=?", (rid,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return rid, iid


def test_whole_assign_and_split(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "user_id": "1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    assert [(r["user_id"], r["share"]) for r in rows] == [(1, 1.0)]
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.4"},
           follow_redirects=True)
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert sorted((r["user_id"], r["share"]) for r in rows) == [(1, 0.6), (2, 0.4)]


def test_bad_shares_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp2.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.3"},
           follow_redirects=True)
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM item_splits WHERE item_id=?", (iid,)).fetchone()[0]
    conn.close()
    assert n == 0


def test_dashboard_user_totals(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp3.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 1, 0.6))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 2, 0.4))
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    assert "Alice" in body and "6.0" in body
