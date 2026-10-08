def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date, item_count, total) "
                 "VALUES ('s.txt','2026-09-05',2,30.0)")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='s.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "BREAD", "Bread", 20.0))
    conn.commit()
    conn.close()
    return rid


def _client(tmp_path, monkeypatch, name):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    rid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client(), rid


def _bodies(c, rid):
    bodies = [c.get(f"/receipts/{rid}").get_data(as_text=True)]
    c.set_cookie("ui", "v2")
    try:
        bodies.append(c.get(f"/receipts/{rid}").get_data(as_text=True))
    finally:
        c.delete_cookie("ui")
    return bodies


def test_summary_default_split(tmp_path, monkeypatch):
    c, rid = _client(tmp_path, monkeypatch, "rsum1.db")
    for body in _bodies(c, rid):
        assert "Alice 15.00" in body and "Bob 15.00" in body
        assert "Default 50-50 split" in body


def test_summary_custom_split(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rsum2.db")
    c.get(f"/receipts/{rid}")  # seeds equal shares
    alice = dbmod.get_db().execute("SELECT id FROM users WHERE name='Alice'").fetchone()["id"]
    i1 = dbmod.get_db().execute(
        "SELECT id FROM items WHERE receipt_id=? AND raw='MILK'", (rid,)).fetchone()["id"]
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(i1), "user_id": str(alice)},
           follow_redirects=True)
    for body in _bodies(c, rid):
        assert "Alice 20.00" in body and "Bob 10.00" in body
        assert "Custom split" in body


def test_summary_updates_live_from_inputs(tmp_path, monkeypatch):
    c, rid = _client(tmp_path, monkeypatch, "rsum3.db")
    for body in _bodies(c, rid):
        assert "updRsum" in body
        assert 'data-rsum' in body


def _ids(dbmod, rid):
    conn = dbmod.get_db()
    rows = conn.execute("SELECT id, raw FROM items WHERE receipt_id=? ORDER BY id",
                        (rid,)).fetchall()
    users = conn.execute("SELECT id, name FROM users ORDER BY name").fetchall()
    conn.close()
    return ({r["raw"]: r["id"] for r in rows},
            {u["name"]: u["id"] for u in users})


def test_bulk_applies_all_rows(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rbulk1.db")
    c.get(f"/receipts/{rid}")  # seeds equal shares
    items, users = _ids(dbmod, rid)
    i1, i2 = items["MILK"], items["BREAD"]
    a, b = users["Alice"], users["Bob"]
    c.post(f"/receipts/{rid}/splits-bulk", data={
        f"share_{i1}_{a}": "0", f"share_{i1}_{b}": "1",
        f"share_{i2}_{a}": "0.3", f"share_{i2}_{b}": "0.7",
    }, follow_redirects=True)
    conn = dbmod.get_db()
    got = {(r["item_id"], r["user_id"]): r["share"] for r in conn.execute(
        "SELECT item_id, user_id, share FROM item_splits").fetchall()}
    conn.close()
    assert got == {(i1, b): 1.0, (i2, a): 0.3, (i2, b): 0.7}


def test_bulk_saves_valid_skips_bad(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rbulk2.db")
    c.get(f"/receipts/{rid}")  # seeds equal shares
    items, users = _ids(dbmod, rid)
    i1, i2 = items["MILK"], items["BREAD"]
    a, b = users["Alice"], users["Bob"]
    body = c.post(f"/receipts/{rid}/splits-bulk", data={
        f"share_{i1}_{a}": "0", f"share_{i1}_{b}": "1",
        f"share_{i2}_{a}": "0.6", f"share_{i2}_{b}": "0.3",
    }, follow_redirects=True).get_data(as_text=True)
    conn = dbmod.get_db()
    got = {(r["item_id"], r["user_id"]): r["share"] for r in conn.execute(
        "SELECT item_id, user_id, share FROM item_splits").fetchall()}
    conn.close()
    assert got[(i1, b)] == 1.0  # valid row applied
    assert got[(i2, a)] == 0.5 and got[(i2, b)] == 0.5  # bad row untouched
    assert "skipped" in body.lower()


def test_bulk_redirect_targets_summary(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rbulk3.db")
    c.get(f"/receipts/{rid}")
    items, users = _ids(dbmod, rid)
    i1 = items["MILK"]
    a, b = users["Alice"], users["Bob"]
    r = c.post(f"/receipts/{rid}/splits-bulk", data={
        f"share_{i1}_{a}": "1", f"share_{i1}_{b}": "0",
    }, follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert r.headers["Location"].endswith(f"/receipts/{rid}#rsum")


def test_bulk_all_failed_has_no_fragment(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rbulk4.db")
    c.get(f"/receipts/{rid}")
    items, users = _ids(dbmod, rid)
    i1 = items["MILK"]
    a, b = users["Alice"], users["Bob"]
    r = c.post(f"/receipts/{rid}/splits-bulk", data={
        f"share_{i1}_{a}": "0.6", f"share_{i1}_{b}": "0.3",
    }, follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "#" not in r.headers["Location"]


def test_single_save_form_replaces_row_forms(tmp_path, monkeypatch):
    import db as dbmod
    c, rid = _client(tmp_path, monkeypatch, "rbulk5.db")
    items, users = _ids(dbmod, rid)
    i1 = items["MILK"]
    a = users["Alice"]
    for body in _bodies(c, rid):
        assert "splits-bulk" in body
        assert f"share_{i1}_{a}" in body
        assert f'action="/receipts/{rid}/splits"' not in body
        assert body.count("Save receipt split") >= 1
