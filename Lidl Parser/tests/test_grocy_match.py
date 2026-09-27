# Lidl Parser/tests/test_grocy_match.py
def test_get_config_missing_raises(tmp_path, monkeypatch):
    import os
    from grocy import GrocyConfigError, get_config
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "nocfg.db"))
    dbmod.init_db()
    old_url = os.environ.pop("GROCY_URL", None)
    old_key = os.environ.pop("GROCY_API_KEY", None)
    try:
        try:
            get_config()
            assert False, "should raise"
        except GrocyConfigError:
            assert True
    finally:
        if old_url is not None:
            os.environ["GROCY_URL"] = old_url
        if old_key is not None:
            os.environ["GROCY_API_KEY"] = old_key


def test_url_trailing_slash_normalized(tmp_path, monkeypatch):
    from grocy import get_config
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "norm.db"))
    dbmod.init_db()
    monkeypatch.setenv("GROCY_URL", "https://x.example.com/api/")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    base, _ = get_config()
    assert base == "https://x.example.com/api"


def test_find_orphans():
    from grocy import find_orphans
    catalog = {"by_id": {5: {"id": 5}}}
    assert find_orphans([5, 99], catalog) == [99]


def test_suggest_barcode_exact_wins():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 7, "name": "Fresh Milk"}, {"id": 9, "name": "Other"}],
        "by_id": {7: {"id": 7, "name": "Fresh Milk"}, 9: {"id": 9, "name": "Other"}},
        "names": ["Fresh Milk", "Other"],
        "barcode_to_ids": {"520123": [7]},
        "cached_at": 0,
    }
    out = suggest_grocy("TOTALLY DIFFERENT", " 520123 ", catalog)
    assert out[0]["grocy_id"] == 7
    assert out[0]["score"] == 100
    assert out[0]["reason"] == "barcode"


def test_suggest_fuzzy_threshold():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 3, "name": "Feta Cheese 400g"}],
        "by_id": {3: {"id": 3, "name": "Feta Cheese 400g"}},
        "names": ["Feta Cheese 400g"],
        "barcode_to_ids": {},
        "cached_at": 0,
    }
    out = suggest_grocy("Feta Cheese 400g", None, catalog)
    assert out and out[0]["grocy_id"] == 3
    out2 = suggest_grocy("zzzz unrelated qqq", None, catalog)
    assert out2 == []


def test_suggest_collision():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        "by_id": {1: {"id": 1, "name": "A"}, 2: {"id": 2, "name": "B"}},
        "names": ["A", "B"],
        "barcode_to_ids": {"111": [1, 2]},
        "cached_at": 0,
    }
    out = suggest_grocy("whatever", "111", catalog)
    assert len(out) == 2
    assert all(o["reason"] == "barcode-collision" for o in out)


def test_suggest_greek_case_insensitive():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 11, "name": "Milk Fresh"}],
        "by_id": {11: {"id": 11, "name": "Milk Fresh"}},
        "names": ["Milk Fresh"],
        "barcode_to_ids": {},
        "cached_at": 0,
    }
    out = suggest_grocy("milk fresh", None, catalog)
    assert out and out[0]["grocy_id"] == 11
    assert out[0]["reason"] == "fuzzy"


def test_products_has_grocy_columns(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    conn.close()
    assert "grocy_id" in cols
    assert "grocy_name" in cols
    assert "grocy_synced_at" in cols


def test_grocy_status_unconfigured(monkeypatch):
    import os
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/api/grocy_status")
    assert r.status_code == 200
    assert r.get_json()["configured"] is False


def test_link_unlink_roundtrip(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t2.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES (?,?,?)", ("RAW1", "Clean 1", "Misc"))
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [{"id": 5, "name": "Clean 1"}], "by_id": {5: {"id": 5, "name": "Clean 1"}}, "names": ["Clean 1"], "barcode_to_ids": {}, "cached_at": 0})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/mappings/link", data={"raw": "RAW1", "grocy_id": "5", "grocy_name": "Clean 1", "_q": ""}, follow_redirects=False)
    assert r.status_code in (302, 303)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id, grocy_name FROM products WHERE raw='RAW1'").fetchone()
    conn.close()
    assert row["grocy_id"] == 5


def test_mappings_shows_grocy_ui(monkeypatch):
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/products")
    assert r.status_code == 200
    assert "Grocy" in r.get_data(as_text=True)


def test_accent_folding_suggests():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 21, "name": "Café Milk"}],
        "by_id": {21: {"id": 21, "name": "Café Milk"}},
        "names": ["Café Milk"],
        "barcode_to_ids": {},
        "cached_at": 0,
    }
    out = suggest_grocy("CAFE MILK", None, catalog)
    assert out and out[0]["grocy_id"] == 21


def test_barcode_dash_normalized():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 7, "name": "Fresh Milk"}],
        "by_id": {7: {"id": 7, "name": "Fresh Milk"}},
        "names": ["Fresh Milk"],
        "barcode_to_ids": {"520123": [7]},
        "cached_at": 0,
    }
    out = suggest_grocy("x", "520-123", catalog)
    assert out and out[0]["grocy_id"] == 7
    assert out[0]["reason"] == "barcode"


def test_grocy_status_hint(monkeypatch):
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/api/grocy_status")
    body = r.get_json()
    assert body["configured"] is False
    assert "hint" in body


def test_mappings_flags_orphan_link(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "orph.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category, grocy_id, grocy_name) VALUES (?,?,?,?,?)",
                 ("RAW-O", "Clean O", "Misc", 99, "Deleted In Grocy"))
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [], "barcode_to_ids": {}, "cached_at": 0})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/products")
    assert r.status_code == 200
    assert "stale" in r.get_data(as_text=True).lower()


def test_stale_cache_fallback(monkeypatch):
    import grocy as g
    g._CACHE.update({"key": None, "at": 0.0, "catalog": None})
    monkeypatch.setenv("GROCY_URL", "http://stale/api")
    monkeypatch.setenv("GROCY_API_KEY", "s3cret")
    base, key = g.get_config()
    monkeypatch.setattr(g, "list_products", lambda *a: [{"id": 1, "name": "A"}])
    monkeypatch.setattr(g, "list_barcodes", lambda *a: [])
    fresh = g.fetch_catalog(base, key)
    assert fresh.get("stale", False) is False
    def _boom(*a):
        raise g.GrocyApiError(0, "unreachable: down")
    monkeypatch.setattr(g, "list_products", _boom)
    import time as _time
    g._CACHE["at"] = _time.time() - 301
    stale = g.fetch_catalog(base, key)
    assert stale.get("stale") is True
    assert stale["products"] == [{"id": 1, "name": "A"}]
