import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "v2.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def test_default_ui_is_v1(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    body = c.get("/").get_data(as_text=True)
    assert "v2.css" not in body
    assert "/upload" in body  # old nav intact


def test_ui_switch_sets_cookie(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/ui/v2")
    assert r.status_code == 302
    assert "ui=v2" in r.headers.get("Set-Cookie", "")
    r = c.get("/ui/v1")
    assert "ui=v1" in r.headers.get("Set-Cookie", "")


def test_v2_dashboard_and_products(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.get("/ui/v2")
    body = c.get("/").get_data(as_text=True)
    assert "v2.css" in body and "kpi-card" in body
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('RAWV2','Clean V2')")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAWV2'").fetchone()[0]
    conn.close()
    body = c.get("/products").get_data(as_text=True)
    assert "v2.css" in body
    assert f'href="/products/{pid}"' in body


def test_v2_detail_receipts_prices_login(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.get("/ui/v2")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('RAWV2','Clean V2')")
    conn.commit()
    pid = conn.execute("SELECT id FROM products WHERE raw='RAWV2'").fetchone()[0]
    conn.close()
    assert c.get(f"/products/{pid}").status_code == 200
    assert "v2.css" in c.get("/prices").get_data(as_text=True)
