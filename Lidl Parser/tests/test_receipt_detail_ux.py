import db as dbmod


def _client(tmp_path, monkeypatch, name="r.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-22')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()[0]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                    vat, discount, total, final)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (rid, "MILK", "Milk", "Misc", 2, 5.0, 13.0, 1.0, 10.0, 9.0))
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('MILK','Milk','Misc')")
    pid = conn.execute("SELECT id FROM products WHERE raw='MILK'").fetchone()[0]
    conn.commit()
    conn.close()
    return rid, pid


def _body(c, rid, v2=False):
    if v2:
        c.set_cookie("ui", "v2")
    try:
        return c.get(f"/receipts/{rid}").get_data(as_text=True)
    finally:
        if v2:
            c.delete_cookie("ui")


def test_minipage_save_propagates_clean_to_items(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    rid, pid = _seed()
    c.post(f"/products/{pid}",
           data={"clean": "Fresh Milk", "category": "Dairy", "grocy_track": "1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    row = conn.execute("SELECT clean, category FROM items WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert tuple(row) == ("Fresh Milk", "Dairy")


def test_receipt_detail_shows_clean_and_full_info(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r2.db")
    rid, pid = _seed()
    for v2 in (False, True):
        body = _body(c, rid, v2)
        assert "Fresh Milk" not in body  # guard: fixture has no such name
        assert ">Milk<" in body or "Milk</a>" in body
        assert "VAT" in body and "13%" in body
        assert "5.00" in body and "1.00" in body


def test_receipt_detail_clean_updates_after_minipage_save(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r3.db")
    rid, pid = _seed()
    c.post(f"/products/{pid}",
           data={"clean": "Fresh Milk", "category": "Dairy", "grocy_track": "1"},
           follow_redirects=True)
    for v2 in (False, True):
        assert "Fresh Milk" in _body(c, rid, v2)


def test_v2_grocy_push_below_items(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r4.db")
    rid, pid = _seed()
    assert 'class="cols"' not in _body(c, rid, True)


def test_split_form_collapsed_behind_button(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r5.db")
    rid, pid = _seed()
    for v2 in (False, True):
        body = _body(c, rid, v2)
        assert "<details" in body
        assert "share_1" in body  # exact controls still available on demand


def test_delete_receipt_confirms(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r6.db")
    _seed()
    assert "confirm(" in c.get("/receipts").get_data(as_text=True)
    c.set_cookie("ui", "v2")
    try:
        assert "confirm(" in c.get("/receipts").get_data(as_text=True)
    finally:
        c.delete_cookie("ui")


def test_resync_names_repairs_stale_items(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r7.db")
    rid, pid = _seed()
    conn = dbmod.get_db()
    conn.execute("UPDATE products SET clean='Fresh Milk', category='Dairy' WHERE raw='MILK'")
    conn.commit()
    conn.close()
    r = c.post("/receipts/resync-names", follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    row = conn.execute("SELECT clean, category FROM items WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert tuple(row) == ("Fresh Milk", "Dairy")
    assert "Resynced names" in r.get_data(as_text=True)
    assert "Fresh Milk" in c.get(f"/receipts/{rid}").get_data(as_text=True)


def test_resync_names_button_on_receipts(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "r8.db")
    _seed()
    assert "resync-names" in c.get("/receipts").get_data(as_text=True)
    c.set_cookie("ui", "v2")
    try:
        assert "resync-names" in c.get("/receipts").get_data(as_text=True)
    finally:
        c.delete_cookie("ui")
