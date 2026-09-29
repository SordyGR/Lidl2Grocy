import db as dbmod


def _client(tmp_path, monkeypatch, name="d.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed_dup():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    conn.execute("INSERT INTO products (raw, clean, category, grocy_id) VALUES ('NOX.ROLL ON TAL.50ML','Nox','Care',99)")
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('NOX.ROLL ON TAL.50ml','Nox','Care')")
    for raw in ("NOX.ROLL ON TAL.50ML", "NOX.ROLL ON TAL.50ml"):
        conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                        VALUES (?,?,?,?,?,?,?,?)""", (rid, raw, "Nox", "Care", 1, 2.0, 2.0, 2.0))
    conn.commit()
    conn.close()


def test_duplicates_page_lists_casefold_groups(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed_dup()
    body = c.get("/products/duplicates").get_data(as_text=True)
    assert "NOX.ROLL ON TAL.50ML" in body and "NOX.ROLL ON TAL.50ml" in body
    assert "winner" in body.lower()


def test_products_banner_points_to_merge(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "d2.db")
    _seed_dup()
    assert "/products/duplicates" in c.get("/products").get_data(as_text=True)


def test_merge_reassigns_and_deletes_loser(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "d3.db")
    _seed_dup()
    r = c.post("/products/duplicates/merge", data={"winner:nox.roll on tal.50ml": "NOX.ROLL ON TAL.50ML"},
               follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    raws = [r[0] for r in conn.execute("SELECT raw FROM products").fetchall()]
    assert raws == ["NOX.ROLL ON TAL.50ML"]
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert conn.execute("SELECT DISTINCT raw FROM items").fetchone()[0] == "NOX.ROLL ON TAL.50ML"
    conn.close()


def test_import_canonicalizes_case_variants(tmp_path, monkeypatch):
    from test_fetch_import import KEPT
    from fetch_lib import import_kept_receipt
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "d4.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    import_kept_receipt(conn, KEPT, "26-09-22.txt", "T1")
    variant = KEPT.replace("TORTI.CHIPS SALT200G", "torti.chips salt200g")
    import_kept_receipt(conn, variant, "26-09-23.txt", "T2")
    raws = [r[0] for r in conn.execute("SELECT raw FROM products ORDER BY raw").fetchall()]
    assert "torti.chips salt200g" not in raws
    assert "TORTI.CHIPS SALT200G" in raws
    conn.close()


def test_merge_transfers_grocy_link_to_winner(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "d5.db")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('WINNER','W')")
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name) VALUES ('winner','W',55,'G55')")
    conn.commit()
    conn.close()
    c.post("/products/duplicates/merge", data={"winner:winner": "WINNER"},
           follow_redirects=True)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id, grocy_name FROM products WHERE raw='WINNER'").fetchone()
    assert tuple(row) == (55, "G55")
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
    conn.close()
