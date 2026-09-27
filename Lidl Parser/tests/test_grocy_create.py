# Lidl Parser/tests/test_grocy_create.py — create missing Grocy products from Mappings.
def test_create_product_posts_and_returns_id(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return {"created_object_id": 55}

    def fake_post(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "post", fake_post)
    gid = g.create_product("http://x/api", "k", "Milk", 4, 3, 3)
    assert gid == 55
    assert calls["url"] == "http://x/api/objects/products"
    assert calls["payload"] == {"name": "Milk", "location_id": 4, "qu_id_purchase": 3, "qu_id_stock": 3}


def test_create_product_400_raises(monkeypatch):
    import grocy as g

    class Resp:
        status_code = 400
        text = "bad"

    monkeypatch.setattr(g.requests, "post", lambda *a, **k: Resp())
    try:
        g.create_product("http://x/api", "k", "", None, None, None)
        assert False, "should raise"
    except g.GrocyApiError as e:
        assert e.status == 400


def test_add_barcode_posts(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return {"created_object_id": 9}

    def fake_post(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "post", fake_post)
    assert g.add_barcode("http://x/api", "k", 55, "520123") == 9
    assert calls["url"] == "http://x/api/objects/product_barcodes"
    assert calls["payload"] == {"product_id": 55, "barcode": "520123"}


def test_add_barcode_with_qu_id(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return {"created_object_id": 9}

    def fake_post(url, headers=None, timeout=None, json=None):
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "post", fake_post)
    assert g.add_barcode("http://x/api", "k", 55, "520123", qu_id=10) == 9
    assert calls["payload"] == {"product_id": 55, "barcode": "520123", "qu_id": 10}


def test_lookups_cached(monkeypatch):
    import grocy as g
    g._LOOKUPS.update({"key": None, "at": 0.0, "data": None})
    hits = {"n": 0}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            hits["n"] += 1
            return [{"id": 4, "name": "Pantry"}]

    monkeypatch.setattr(g.requests, "get", lambda *a, **k: Resp())
    first = g.fetch_lookups("http://x/api", "k")
    second = g.fetch_lookups("http://x/api", "k")
    assert first["locations"] == [{"id": 4, "name": "Pantry"}]
    assert second == first
    assert hits["n"] == 2


def test_create_route_creates_and_links(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, barcode) VALUES ('MILKX','Milk X','520999')")
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "create_product", lambda *a: 77)
    monkeypatch.setattr(g, "add_barcode", lambda *a: 8)
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/mappings/create-in-grocy", data={
        "raw": "MILKX", "name": "Milk X", "barcode": "520999",
        "location_id": "4", "qu_purchase": "3", "qu_stock": "3",
    }, follow_redirects=True)
    assert "Created Grocy #77 and linked" in r.get_data(as_text=True)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id, grocy_name FROM products WHERE raw='MILKX'").fetchone()
    conn.close()
    assert row["grocy_id"] == 77
    assert row["grocy_name"] == "Milk X"


def test_create_route_failure_stores_nothing(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c2.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('MILKX','Milk X')")
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    def boom(*a):
        raise g.GrocyApiError(400, "bad product")
    monkeypatch.setattr(g, "create_product", boom)
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/mappings/create-in-grocy", data={"raw": "MILKX", "name": "Milk X"}, follow_redirects=True)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id FROM products WHERE raw='MILKX'").fetchone()
    conn.close()
    assert row["grocy_id"] is None
