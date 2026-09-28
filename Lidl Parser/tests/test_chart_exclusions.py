import json

import db as dbmod


def _client(tmp_path, monkeypatch, name="e.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
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
    conn.commit()
    conn.close()


def _exclude(*cats):
    import settings_store as ss
    ss.set("EXCLUDED_CATEGORIES", json.dumps(list(cats)))


def test_excluded_vanishes_from_charts_but_not_counts(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    _exclude("Alcohol")
    body = c.get("/").get_data(as_text=True)
    assert "Alcohol" not in body
    assert "Dairy" in body
    import re
    m = re.search(r"Total spent</span><b>([\d.]+)", body)
    assert m and abs(float(m.group(1)) - 10.0) < 0.01
    assert "Items</span><b>2" in body  # counts stay absolute


def test_abc_excludes_and_reports_distribution(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "e2.db")
    _seed()
    conn = dbmod.get_db()
    rid = conn.execute("SELECT id FROM receipts").fetchone()[0]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                    VALUES (?,?,?,?,?,?,?,?)""", (rid, "BREAD", "Bread", "Bakery", 1, 5.0, 5.0, 5.0))
    conn.commit()
    conn.close()
    _exclude("Alcohol")
    j = c.get("/api/abc_analysis?metric=spend&top=50").get_json()
    assert [r["clean"] for r in j["rows"]] == ["Milk", "Bread"]
    assert abs(j["total"] - 15.0) < 0.01
    assert j["count"] == 2
    assert j["classes"] == {"A": 1, "B": 0, "C": 1}  # 10/15=66.7 → A, then 100 → C


def test_settings_roundtrip_excluded(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "e3.db")
    _seed()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('MILK','Milk','Dairy')")
    conn.commit()
    conn.close()
    r = c.post("/settings", data={"form": "excluded", "excluded": ["Dairy"]},
               follow_redirects=True)
    assert r.status_code == 200
    body = c.get("/settings").get_data(as_text=True)
    assert "Excluded from charts" in body
    assert 'value="Dairy" checked' in body or 'checked' in body
    import settings_store as ss
    assert json.loads(ss.get("EXCLUDED_CATEGORIES")) == ["Dairy"]


def test_grocy_save_does_not_wipe_excluded(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "e4.db")
    _seed()
    _exclude("Alcohol")
    c.post("/settings", data={"form": "grocy", "GROCY_URL": "https://g.example/api",
                              "GROCY_API_KEY": "k"}, follow_redirects=True)
    import settings_store as ss
    assert json.loads(ss.get("EXCLUDED_CATEGORIES")) == ["Alcohol"]
    assert ss.get("GROCY_URL") == "https://g.example/api"
