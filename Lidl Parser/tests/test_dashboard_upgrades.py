import db as dbmod


def _client(tmp_path, monkeypatch, name="u.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                    VALUES (?,?,?,?,?,?,?,?)""", (rid, "MILK", "Milk", "Dairy", 1, 10.0, 10.0, 10.0))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                    VALUES (?,?,?,?,?,?,?,?)""", (rid, "WINE", "Wine", "Alcohol", 1, 5.0, 5.0, 5.0))
    conn.execute("INSERT INTO products (raw, clean, category, grocy_id, grocy_name) VALUES ('MILK','Milk','Dairy',7,'Milk')")
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('WINE','Wine','Alcohol')")
    conn.commit()
    conn.close()
    return rid


def test_dashboard_pie_center_total(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    body = c.get("/").get_data(as_text=True)
    assert "pie-total" in body or "pieTotal" in body or "centerText" in body
    assert "15.00" in body


def test_abc_group_category_ranked(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "u2.db")
    _seed()
    j = c.get("/api/abc_analysis?metric=spend&top=50&group=category").get_json()
    assert j["group"] == "category"
    cleans = [r["clean"] for r in j["rows"]]
    assert cleans == ["Dairy", "Alcohol"]
    assert j["rows"][0]["rank"] == 1
    assert j["rows"][1]["rank"] == 2
    assert "cum_pct" in j["rows"][0] and "class" in j["rows"][0]


def test_pareto_cumulative_on_top_and_badges(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "u3.db")
    _seed()
    body = c.get("/prices").get_data(as_text=True)
    assert "group" in body and "category" in body
    # cumulative line dataset must paint above bars
    assert "order: 1" in body or "order:1" in body
    assert "#1" in body or "rank" in body.lower()


def test_products_list_badge_only_and_rank(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "u4.db")
    _seed()
    import settings_store as ss
    ss.set("GROCY_URL", "https://g.example/api")
    ss.set("GROCY_API_KEY", "k")
    body = c.get("/products").get_data(as_text=True)
    assert "/mappings/link" not in body
    assert "https://g.example/product/7" in body
    assert "abc" in body.lower() or "pareto" in body.lower() or "#1" in body


def test_product_detail_has_workflow(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "u5.db")
    _seed()
    import settings_store as ss
    ss.set("GROCY_URL", "https://g.example/api")
    ss.set("GROCY_API_KEY", "k")
    conn = dbmod.get_db()
    pid = conn.execute("SELECT id FROM products WHERE raw='MILK'").fetchone()[0]
    conn.close()
    body = c.get(f"/products/{pid}").get_data(as_text=True)
    assert "/mappings/unlink" in body or "Unlink" in body
    assert "https://g.example/product/7" in body or "product/7" in body


def test_receipt_download_txt(tmp_path, monkeypatch):
    import os
    import app as appmod
    c = _client(tmp_path, monkeypatch, "u6.db")
    rid = _seed()
    os.makedirs(os.path.join(appmod.BASE_DIR, "receipts"), exist_ok=True)
    with open(os.path.join(appmod.BASE_DIR, "receipts", "a.txt"), "w", encoding="utf-8") as f:
        f.write("hello receipt")
    r = c.get(f"/receipts/{rid}/download")
    assert r.status_code == 200
    assert "hello receipt" in r.get_data(as_text=True)
    assert "text/plain" in r.headers.get("Content-Type", "")
