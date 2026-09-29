import db as dbmod


def _client(tmp_path, monkeypatch, name="m.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('R1','C1','dairy')")
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('R2','C2','MyCat')")
    for raw, cat in (("R1", "dairy"), ("R2", "MyCat")):
        conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                        VALUES (?,?,?,?,?,?,?,?)""", (rid, raw, raw, cat, 1, 1.0, 1.0, 1.0))
    conn.execute("INSERT INTO categories (grocy_id, name) VALUES (1,'Dairy')")
    conn.execute("INSERT INTO categories (grocy_id, name) VALUES (2,'Bakery')")
    conn.commit()
    conn.close()


def test_merge_page_marks_matches_and_offers_mapping(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    body = c.get("/categories/merge").get_data(as_text=True)
    assert "dairy" in body and "MyCat" in body
    assert "matches Grocy" in body and "Dairy" in body
    assert "Bakery" in body  # available option
    assert "keep mine" in body.lower()


def test_merge_applies_mapping_and_keeps_rest(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "m2.db")
    _seed()
    r = c.post("/categories/merge", data={"map:dairy": "Dairy", "map:MyCat": "__keep__"},
               follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    assert conn.execute("SELECT category FROM products WHERE raw='R1'").fetchone()[0] == "Dairy"
    assert conn.execute("SELECT category FROM products WHERE raw='R2'").fetchone()[0] == "MyCat"
    assert [tuple(r) for r in conn.execute("SELECT DISTINCT category FROM items ORDER BY 1").fetchall()] == [("Dairy",), ("MyCat",)]
    conn.close()
    assert "renamed" in r.get_data(as_text=True).lower()
