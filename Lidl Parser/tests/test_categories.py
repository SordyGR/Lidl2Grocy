import db as dbmod


def _client(tmp_path, monkeypatch, name="c.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed_cache():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO categories (grocy_id, name, source) VALUES (7, 'Dairy', 'grocy')")
    conn.commit()
    conn.close()


def test_refresh_upserts_and_lists(tmp_path, monkeypatch):
    import grocy as g
    monkeypatch.setattr(g, "fetch_product_groups",
                        lambda *a: [{"id": 7, "name": "Dairy"}, {"id": 9, "name": "Bakery"}])
    monkeypatch.setenv("GROCY_URL", "https://x.example/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    c = _client(tmp_path, monkeypatch)
    r = c.post("/settings/categories-refresh", follow_redirects=True)
    assert r.status_code == 200
    body = c.get("/settings").get_data(as_text=True)
    assert "Dairy" in body and "Bakery" in body
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    assert n == 2


def test_refresh_failure_keeps_cache(tmp_path, monkeypatch):
    import grocy as g

    def _boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(g, "fetch_product_groups", _boom)
    c = _client(tmp_path, monkeypatch, "c2.db")
    _seed_cache()
    r = c.post("/settings/categories-refresh", follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    row = conn.execute("SELECT name FROM categories WHERE grocy_id=7").fetchone()
    conn.close()
    assert row is not None and row[0] == "Dairy"


def test_new_item_falls_back_to_cache(tmp_path, monkeypatch):
    import grocy as g

    def _boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(g, "fetch_product_groups", _boom)
    monkeypatch.setenv("GROCY_URL", "https://x.example/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    c = _client(tmp_path, monkeypatch, "c3.db")
    _seed_cache()
    body = c.get("/mappings/new-item?raw=RAWX").get_data(as_text=True)
    assert "Dairy" in body
    assert "cached" in body.lower()


def test_hub_suggests_cached_categories(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "c4.db")
    _seed_cache()
    body = c.get("/products").get_data(as_text=True)
    assert 'id="grocy-cats"' in body
    assert "Dairy" in body
