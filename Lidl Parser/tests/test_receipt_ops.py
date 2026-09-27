import db as dbmod


def _client(tmp_path, monkeypatch, name="ops.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed_receipt(label="26-09-01.txt"):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, item_count, total) VALUES (?, '2026-09-01', 1, 2.0)",
                 (label,))
    rid = conn.execute("SELECT id FROM receipts WHERE filename=?", (label,)).fetchone()[0]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, qty, unit_price, total, final) VALUES (?,?,?,?,?,?,?)",
                 (rid, "X", "X", 1, 2.0, 2.0, 2.0))
    iid = conn.execute("SELECT id FROM items WHERE receipt_id=?", (rid,)).fetchone()[0]
    conn.execute("INSERT INTO users (name) VALUES ('U1')")
    uid = conn.execute("SELECT id FROM users WHERE name='U1'").fetchone()[0]
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)", (iid, uid))
    conn.execute("INSERT INTO grocy_pushes (receipt_id, pushed_count, skipped_count, errors) VALUES (?,1,0,'[]')",
                 (rid,))
    conn.commit()
    conn.close()
    return rid


def test_v2_receipts_and_upload(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.get("/ui/v2")
    assert "v2.css" in c.get("/receipts").get_data(as_text=True)
    body = c.get("/upload").get_data(as_text=True)
    assert "v2.css" in body and 'type="file"' in body


def test_receipt_delete_cascades(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    rid = _seed_receipt()
    r = c.post(f"/receipts/{rid}/delete", follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    assert conn.execute("SELECT 1 FROM receipts WHERE id=?", (rid,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM items WHERE receipt_id=?", (rid,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM item_splits").fetchone() is None
    assert conn.execute("SELECT 1 FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone() is None
    conn.close()
    assert c.post("/receipts/999999/delete").status_code == 404


def test_receipt_card_override(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    rid = _seed_receipt()
    r = c.post(f"/receipts/{rid}/card", data={"last4": "9366"}, follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    assert conn.execute("SELECT card_last4 FROM receipts WHERE id=?", (rid,)).fetchone()[0] == "9366"
    conn.close()
    r = c.post(f"/receipts/{rid}/card", data={"last4": "12ab"})
    assert r.status_code == 400
    assert c.post("/receipts/999999/card", data={"last4": "9366"}).status_code == 404


def test_card_extractor_tolerant_variants():
    from receipt_parser import extract_card_last4
    assert extract_card_last4("xxx\n  ************9366  \n") == "9366"
    assert extract_card_last4(" terminal\n****9366 approved\nΣΥΝΟΛΟ 3,77\n") == "9366"
    assert extract_card_last4("ΚΑΡΤΑ **** 9366\n") == "9366"
    assert extract_card_last4("Mastercard ****9366 Auth. 123456\n") == "9366"
    assert extract_card_last4("no card here\nΣΥΝΟΛΟ 3,77\n") is None


def test_secret_roundtrip_and_legacy(tmp_path, monkeypatch):
    import secret_store as ss
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(ss, "KEY_PATH", str(tmp_path / ".lidl-key"))
    dbmod.init_db()
    ss.set_secret("LIDL_PASS", "s3cr3t!")
    assert ss.get_secret("LIDL_PASS") == "s3cr3t!"
    raw = dbmod.get_db().execute("SELECT value FROM settings WHERE key='LIDL_PASS'").fetchone()[0]
    assert raw != "s3cr3t!" and raw.startswith("enc$")
    # legacy plaintext still readable
    conn = dbmod.get_db()
    conn.execute("UPDATE settings SET value='plain' WHERE key='LIDL_PASS'")
    conn.commit()
    conn.close()
    assert ss.get_secret("LIDL_PASS") == "plain"
    ss.clear_secret("LIDL_PASS")
    assert ss.get_secret("LIDL_PASS") == ""
