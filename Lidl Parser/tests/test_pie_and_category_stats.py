import re

import db as dbmod


def _client(tmp_path, monkeypatch, name="p.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed_many_cats():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    for i in range(13):
        conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (rid, f"RAW{i}", f"Clean{i}", f"Cat{i:02d}", 1, 10.0, 10.0, 10.0))
    conn.commit()
    conn.close()


def _seed_cat_lines():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('a.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='a.txt'").fetchone()[0]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (rid, "MILK", "Milk", "Dairy", 2, 5.0, 10.0, 10.0))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (rid, "CHEESE", "Cheese", "Dairy", 1, 7.0, 7.0, 7.0))
    conn.commit()
    conn.close()


def test_pie_total_matches_kpi_all_time(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed_many_cats()
    body = c.get("/").get_data(as_text=True)
    kpi = float(re.search(r"Total spent</span><b>([\d.]+)", body).group(1))
    pie = float(re.search(r'id="pie-total">([\d.]+)', body).group(1))
    assert kpi == 130.0
    assert pie == kpi
    assert "Other" in body


def test_category_history_feeds_stats(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "p2.db")
    _seed_cat_lines()
    j = c.get("/api/category_history?category=Dairy").get_json()
    pts = j["Dairy"]
    assert len(pts) == 2
    assert sum(p["q"] for p in pts) == 3
    assert sorted(p["p"] for p in pts) == [5.0, 7.0]


def test_pareto_before_trend(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "p3.db")
    _seed_cat_lines()
    for cookies in ({}, {"ui": "v2"}):
        client = c
        if cookies:
            client.set_cookie("ui", "v2")
        try:
            body = client.get("/prices").get_data(as_text=True)
        finally:
            if cookies:
                client.delete_cookie("ui")
        assert body.index('id="pareto"') < body.index('id="trend"')
