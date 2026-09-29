# Lidl Parser/tests/test_grocy_push.py
def test_add_stock_posts_purchase(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return [{"id": 1, "product_id": 7, "stock_id": "44"}]

    def fake_post(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "post", fake_post)
    out = g.add_stock("http://x/api", "k", 7, 2.0, 1.29, "Lidl 26-09-05.txt")
    assert out[0]["stock_id"] == "44"
    assert calls["url"] == "http://x/api/stock/products/7/add"
    assert calls["payload"]["transaction_type"] == "purchase"
    assert calls["payload"]["amount"] == 2.0
    assert calls["payload"]["price"] == 1.29
    assert "best_before_date" not in calls["payload"]


def test_add_stock_400_raises(monkeypatch):
    import grocy as g

    class Resp:
        status_code = 400
        text = "Not existing product"

    monkeypatch.setattr(g.requests, "post", lambda *a, **k: Resp())
    try:
        g.add_stock("http://x/api", "k", 999, 1, 1.0, "n")
        assert False, "should raise"
    except g.GrocyApiError as e:
        assert e.status == 400


def test_set_purchased_date_puts(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return []

    def fake_put(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "put", fake_put)
    g.set_purchased_date("http://x/api", "k", "44", "2026-09-05")
    assert calls["url"] == "http://x/api/stock/entry/44"
    assert calls["payload"] == {"purchased_date": "2026-09-05"}


def test_push_schema(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    assert "grocy_track" in cols
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "grocy_pushes" in tables
    conn.execute("INSERT INTO receipts (filename) VALUES ('r.txt')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO grocy_pushes (receipt_id, pushed_count, skipped_count, errors) VALUES (?,?,?,?)",
                 (rid, 3, 1, '[]'))
    conn.commit()
    row = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert row["pushed_count"] == 3


def test_track_defaults_to_one(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p2.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('R','C')")
    conn.commit()
    row = conn.execute("SELECT grocy_track FROM products WHERE raw='R'").fetchone()
    conn.close()
    assert row["grocy_track"] == 1


def _seed_receipt(dbmod, catalog):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('26-09-05.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='26-09-05.txt'").fetchone()["id"]
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name, grocy_track) VALUES ('MILK','Milk',7,'Milk',1)")
    conn.execute("INSERT INTO products (raw, clean, grocy_track) VALUES ('SOAP','Soap',0)")
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'MILK','Milk',2,1.29,0.0,2.58,2.58)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'SOAP','Soap',1,0.99,0.0,0.99,0.99)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'NEWM','New M',1,5.0,0.0,5.0,5.0)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'ZERO','Zero',0,1.0,0.0,0.0,0.0)""", (rid,))
    conn.commit()
    conn.close()
    return rid


def test_preview_statuses(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "v.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}/grocy-preview", headers={"Accept": "application/json"})
    assert r.status_code == 200
    body = r.get_json()
    by_raw = {i["raw"]: i for i in body["items"]}
    assert by_raw["MILK"]["status"] == "ready"
    assert by_raw["MILK"]["price"] == 1.29
    assert by_raw["SOAP"]["status"] == "excluded-flagged"
    assert by_raw["NEWM"]["status"] == "excluded-no-link"
    assert by_raw["ZERO"]["status"] == "bad-quantity"


def test_push_adds_backdates_logs(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "w.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    added, edited = [], []
    monkeypatch.setattr(g, "add_stock", lambda *a: (added.append(a), [{"stock_id": "44"}])[1])
    monkeypatch.setattr(g, "set_purchased_date", lambda *a: edited.append(a))
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert len(added) == 1
    assert added[0][2] == 7
    assert edited and edited[0][3] == "2026-09-05"
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count, skipped_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1
    assert log["skipped_count"] == 2
    assert "ZERO" in log["errors"]
    r2 = c.post(f"/receipts/{rid}/grocy-push", follow_redirects=True)
    assert len(added) == 1
    assert "duplicate" in r2.get_data(as_text=True).lower()
    r3 = c.post(f"/receipts/{rid}/grocy-push", data={"force": "1"}, follow_redirects=False)
    assert len(added) == 2


def test_push_item_400_continues(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "e.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    def boom(*a):
        raise g.GrocyApiError(400, "Not existing product")
    monkeypatch.setattr(g, "add_stock", boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 0
    assert "MILK" in log["errors"]


def test_backdate_failure_still_pushed(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "b.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    monkeypatch.setattr(g, "add_stock", lambda *a: [{"stock_id": "44"}])
    def boom(*a):
        raise g.GrocyApiError(400, "bad entry")
    monkeypatch.setattr(g, "set_purchased_date", boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1
    assert "backdate" in log["errors"].lower() or "MILK" in log["errors"]


def test_missing_stock_id_skips_backdate(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    monkeypatch.setattr(g, "add_stock", lambda *a: [{"id": 1}])
    edited = []
    monkeypatch.setattr(g, "set_purchased_date", lambda *a: edited.append(a))
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    assert edited == []
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1


def test_track_toggle(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('R','C')")
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/mappings/track", data={"raw": "R", "track": "0"}, follow_redirects=False)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_track FROM products WHERE raw='R'").fetchone()
    conn.close()
    assert row["grocy_track"] == 0


def test_receipt_page_shows_preview_and_push(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Grocy" in html
    assert "grocy-push" in html
    assert "ready" in html
    assert "excluded-flagged" in html


def test_mappings_shows_track_toggle(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "m.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, grocy_track) VALUES ('R','C',0)")
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/products")
    assert r.status_code == 200
    assert "Linked" in r.get_data(as_text=True) or "—" in r.get_data(as_text=True)
    pid = dbmod.get_db().execute("SELECT id FROM products WHERE raw='R'").fetchone()[0]
    dbmod.get_db().close()
    d = c.get(f"/products/{pid}").get_data(as_text=True)
    assert "grocy_track" in d


def _details(qu_purchase=10, qu_stock=11, factor=200.0):
    return {"id": 7, "name": "Milk", "qu_id_purchase": qu_purchase,
            "qu_id_stock": qu_stock,
            "qu_conversion_factor_purchase_to_stock": factor,
            "default_quantity_unit_purchase": {"id": qu_purchase, "name": "Piece"}}


def test_converts_purchase_to_stock_units():
    import grocy as g
    amount, factor = g.convert_to_stock_amount(1.0, _details())
    assert amount == 200.0
    assert factor == 200.0


def test_no_conversion_when_units_match():
    import grocy as g
    amount, factor = g.convert_to_stock_amount(2.0, _details(qu_purchase=3, qu_stock=3, factor=1.0))
    assert amount == 2.0
    assert factor == 1.0


def test_missing_factor_falls_back_to_one():
    import grocy as g
    amount, factor = g.convert_to_stock_amount(1.0, {"id": 7})
    assert amount == 1.0
    assert factor == 1.0


def test_preview_converts_to_stock_units(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "cv.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)

    def _no_live_details(*a):
        raise AssertionError("push must use stored factors, not live details")

    monkeypatch.setattr(g, "get_product_details", _no_live_details)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    conn = dbmod.get_db()
    conn.execute("UPDATE products SET qu_factor=200.0, purchase_qu_id=10 WHERE raw='MILK'")
    conn.execute("INSERT OR IGNORE INTO units (grocy_id, name, source) VALUES (10, 'Piece', 'grocy')")
    conn.commit()
    conn.close()
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}/grocy-preview", headers={"Accept": "application/json"})
    assert r.status_code == 200
    by_raw = {i["raw"]: i for i in r.get_json()["items"]}
    assert by_raw["MILK"]["amount"] == 400.0
    assert by_raw["MILK"]["price"] == 2.58 / 400.0
    assert by_raw["MILK"]["qu_factor"] == 200.0


def test_converted_price_keeps_full_precision(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "cp.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name, grocy_track, qu_factor) VALUES ('CHEESE','Cheese',7,'Cheese',1,200.0)")
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'CHEESE','Cheese',1,2.39,0.0,2.39,2.39)""", (rid,))
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Cheese"}], "by_id": {7: {"id": 7, "name": "Cheese"}},
               "names": ["Cheese"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)

    def _no_live_details(*a):
        raise AssertionError("push must use stored factors, not live details")

    monkeypatch.setattr(g, "get_product_details", _no_live_details)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}/grocy-preview", headers={"Accept": "application/json"})
    item = r.get_json()["items"][0]
    assert item["amount"] == 200.0
    assert item["price"] == 2.39 / 200.0
    assert item["price"] * item["amount"] == 2.39


def test_get_product_details_calls_stock_endpoint(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return {"product": {"id": 7}}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        return Resp()

    monkeypatch.setattr(g.requests, "get", fake_get)
    out = g.get_product_details("http://x/api", "k", 7)
    assert calls["url"] == "http://x/api/stock/products/7"
    assert out["product"]["id"] == 7


def test_bad_quantity_fails_not_skipped(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "q.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {})
    monkeypatch.setattr(g, "add_stock", lambda *a: [{"stock_id": "44"}])
    monkeypatch.setattr(g, "set_purchased_date", lambda *a: None)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post(f"/receipts/{rid}/grocy-push", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "failed 1" in html
    conn = dbmod.get_db()
    import json as _json
    log = conn.execute("SELECT pushed_count, skipped_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1
    assert log["skipped_count"] == 2
    assert "ZERO" in log["errors"]
    assert "bad quantity" in log["errors"]


def test_push_aborts_when_grocy_down(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "d.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    def boom(*a):
        raise g.GrocyApiError(0, "unreachable: down")
    monkeypatch.setattr(g, "fetch_catalog", boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog=None)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO grocy_pushes (receipt_id, pushed_count, skipped_count, errors) VALUES (?,?,?,?)",
                 (rid, 5, 0, '[]'))
    conn.commit()
    before = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()["pushed_count"]
    conn.close()
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post(f"/receipts/{rid}/grocy-push", data={"force": "1"}, follow_redirects=True)
    assert "aborted" in r.get_data(as_text=True).lower()
    conn = dbmod.get_db()
    after = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()["pushed_count"]
    conn.close()
    assert after == before == 5


def test_push_uses_stored_per_raw_factor(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "pf.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)

    def _must_not_call(*a):
        raise AssertionError("push must use stored factors, not live details")

    monkeypatch.setattr(g, "get_product_details", _must_not_call)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name, qu_factor) VALUES ('MILK','Milk',7,'Milk',1)")
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name, qu_factor) VALUES ('MINI','Milk mini',7,'Milk',200)")
    conn.execute("INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final) VALUES (?, 'MILK','Milk',2,1.29,0,2.58,2.58)", (rid,))
    conn.execute("INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final) VALUES (?, 'MINI','Milk mini',2,0.50,0,1.00,1.00)", (rid,))
    conn.commit()
    conn.close()
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}/grocy-preview", headers={"Accept": "application/json"})
    assert r.status_code == 200
    by_raw = {i["raw"]: i for i in r.get_json()["items"]}
    assert by_raw["MILK"]["amount"] == 2.0
    assert by_raw["MINI"]["amount"] == 400.0
    assert by_raw["MINI"]["price"] == 1.00 / 400.0


def test_link_prefills_factor_from_grocy(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "lp.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('RAW9','Clean 9')")
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "get_product_details", lambda *a: {
        "qu_conversion_factor_purchase_to_stock": 200.0, "qu_id_purchase": 10,
        "default_quantity_unit_purchase": {"id": 10, "name": "Piece"}})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/mappings/link", data={"raw": "RAW9", "grocy_id": "7", "grocy_name": "Milk", "_q": ""})
    conn = dbmod.get_db()
    row = conn.execute("SELECT qu_factor, purchase_qu_id FROM products WHERE raw='RAW9'").fetchone()
    conn.close()
    assert row["qu_factor"] == 200.0 and row["purchase_qu_id"] == 10


def test_save_barcode_falls_back_without_qu_id(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "bc.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    calls = []

    def fake_add(base_url, api_key, pid, barcode, qu_id=None):
        calls.append(qu_id)
        if qu_id is not None:
            raise g.GrocyApiError(400, "unknown field qu_id")
        return 9

    monkeypatch.setattr(g, "add_barcode", fake_add)
    assert appmod._save_barcode("http://x/api", "k", 7, "520123", 10) == (9, False)
    assert calls == [10, None]
