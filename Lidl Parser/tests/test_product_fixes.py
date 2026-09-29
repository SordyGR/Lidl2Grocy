import db as dbmod


def _client(tmp_path, monkeypatch, name="f.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    appmod.app.testing = True
    return appmod.app.test_client()


def test_price_history_points_come_out_sorted(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-21')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('b.txt','2026-09-23')")
    r1 = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    r2 = conn.execute("SELECT id FROM receipts WHERE filename='b.txt'").fetchone()[0]
    for rid in (r1, r2):
        conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, total, final)
                        VALUES (?,?,?,?,?,?,?)""", (rid, "X", "XJac", 1, 1.0, 1.0, 1.0))
    conn.execute("INSERT INTO price_history (clean, date, price) VALUES ('XJac','2026-09-22',2.0)")
    conn.commit()
    conn.close()
    j = c.get("/api/price_history?clean=XJac").get_json()
    ds = [p["d"] for p in j["points"]]
    assert ds == sorted(ds) and len(ds) == 3


def test_product_category_is_dropdown(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "f2.db")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('RAWX','Clean X','Dairy')")
    conn.execute("INSERT INTO categories (grocy_id, name) VALUES (3,'Bakery')")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAWX'").fetchone()[0]
    conn.close()
    for url in (f"/products/{pid}",):
        body = c.get(url).get_data(as_text=True)
        assert '<select name="category"' in body
        assert "Dairy" in body and "Bakery" in body
    r = c.post(f"/products/{pid}", data={"clean": "Clean X", "category": "Bakery",
                                         "grocy_track": "1"}, follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    assert conn.execute("SELECT category FROM products WHERE id=?", (pid,)).fetchone()[0] == "Bakery"
    conn.close()


def test_pareto_cum_line_has_explicit_contrast_color():
    import os
    for tpl in ("templates/prices.html", "templates/prices_v2.html"):
        src = open(os.path.join(os.path.dirname(__file__), "..", tpl), encoding="utf-8").read()
        i = src.find("cum %")
        assert i != -1
        assert "borderColor" in src[i:i + 400], tpl
