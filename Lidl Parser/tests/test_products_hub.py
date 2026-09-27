import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "h.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed(n, stub=0):
    conn = dbmod.get_db()
    for i in range(n):
        conn.execute("INSERT INTO products (raw, clean, needs_review) VALUES (?,?,?)",
                     (f"RAW{i:03d}", f"Clean {i}", 1 if i < stub else 0))
    conn.commit()
    conn.close()


def test_products_route_and_redirect(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/products").status_code == 200
    assert "Products" in c.get("/products").get_data(as_text=True)
    r = c.get("/mappings?q=x&page=2")
    assert r.status_code == 301 and r.headers["Location"].endswith("/products?q=x&page=2")


def test_page_size_and_review_filter(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed(30, stub=2)
    body = c.get("/products?per=25").get_data(as_text=True)
    assert "page 1 of 2" in body
    body = c.get("/products?filter=needs-review").get_data(as_text=True)
    assert "RAW000" in body and "RAW002" not in body


def test_api_products_greek_search(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('ALFA','Άλφα γάλα')")
    conn.commit()
    conn.close()
    j = c.get("/api/products?q=αλφα γαλα").get_json()
    assert [r["raw"] for r in j["rows"]] == ["ALFA"]
    assert j["count"] == 1


def test_api_products_rejects_bad_params(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed(3)
    j = c.get("/api/products?per=999&sort=bogus&page=abc").get_json()
    assert j["page"] == 1 and j["pages"] == 1 and j["count"] == 3


def _seed_receipt(db_conn):
    db_conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('26-09-01.txt','2026-09-01')")
    rid = db_conn.execute("SELECT id FROM receipts WHERE filename='26-09-01.txt'").fetchone()[0]
    db_conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, total, final)
                       VALUES (?, 'RAW007', 'Clean 7', 2, 1.50, 3.00, 3.00)""", (rid,))
    db_conn.commit()
    return rid


def test_mini_page_shows_attributes_and_receipts(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, barcode, brand) VALUES ('RAW007','Clean 7','520123','BrandX')")
    conn.commit()
    rid = _seed_receipt(conn)
    pid = conn.execute("SELECT id FROM products WHERE raw='RAW007'").fetchone()[0]
    conn.close()
    body = c.get(f"/products/{pid}").get_data(as_text=True)
    assert "BrandX" in body and "520123" in body and "26-09-01.txt" in body


def test_mini_page_save_clears_review(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, needs_review) VALUES ('RAW008','Clean 8',1)")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAW008'").fetchone()[0]
    conn.close()
    r = c.post(f"/products/{pid}", data={"clean": "Clean Eight", "category": "Dairy"}, follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    row = conn.execute("SELECT clean, needs_review FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert row["clean"] == "Clean Eight" and row["needs_review"] == 0


def test_mini_page_404(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/products/999999").status_code == 404


def test_receipt_links_to_mini_page(tmp_path, monkeypatch):
    import grocy as g
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: (_ for _ in ()).throw(g.GrocyConfigError("x")))
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('RAW007','Clean 7')")
    conn.commit()
    rid = _seed_receipt(conn)
    pid = conn.execute("SELECT id FROM products WHERE raw='RAW007'").fetchone()[0]
    conn.close()
    body = c.get(f"/receipts/{rid}").get_data(as_text=True)
    assert f'href="/products/{pid}"' in body


def test_mini_page_untrack_saves_zero(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, grocy_track) VALUES ('RAW010','Clean 10',1)")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAW010'").fetchone()[0]
    conn.close()
    # unchecked checkbox submits nothing for grocy_track (hidden 0 carries the intent)
    c.post(f"/products/{pid}", data={"clean": "Clean 10"})
    conn = dbmod.get_db()
    track = conn.execute("SELECT grocy_track FROM products WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert track == 0


def test_mini_page_barcode_change_syncs_grocy(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import grocy as g
    calls = []
    monkeypatch.setattr(g, "add_barcode", lambda *a, **k: calls.append(k) or 11)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, grocy_id, barcode, purchase_qu_id) VALUES ('RAW011','C11',7,'OLD',10)")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAW011'").fetchone()[0]
    conn.close()
    c.post(f"/products/{pid}", data={"clean": "C11", "barcode": "NEW", "grocy_track": "1"})
    assert calls and calls[0].get("qu_id") == 10
    c.post(f"/products/{pid}", data={"clean": "C11", "barcode": "NEW", "grocy_track": "1"})
    assert len(calls) == 1


def test_new_item_create_posts_barcode_with_size(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import grocy as g
    calls = {}
    monkeypatch.setattr(g, "create_product", lambda *a, **k: 77)
    def fake_add(base_url, api_key, pid, barcode, qu_id=None):
        calls["qu_id"] = qu_id
        return 8
    monkeypatch.setattr(g, "add_barcode", fake_add)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('RAW012','C12')")
    conn.commit()
    conn.close()
    c.post("/mappings/new-item", data={"raw": "RAW012", "name": "C12", "barcode": "520777", "qu_purchase": "10"})
    assert calls.get("qu_id") == 10
