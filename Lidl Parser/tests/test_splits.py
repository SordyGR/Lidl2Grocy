def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    iid = conn.execute("SELECT id FROM items WHERE receipt_id=?", (rid,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return rid, iid


def test_whole_assign_and_split(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "user_id": "1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    assert [(r["user_id"], r["share"]) for r in rows] == [(1, 1.0)]
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.4"},
           follow_redirects=True)
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert sorted((r["user_id"], r["share"]) for r in rows) == [(1, 0.6), (2, 0.4)]


def test_bad_shares_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp2.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.3"},
           follow_redirects=True)
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM item_splits WHERE item_id=?", (iid,)).fetchone()[0]
    conn.close()
    assert n == 0


def test_dashboard_user_totals(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp3.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 1, 0.6))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 2, 0.4))
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    assert "Alice" in body and "6.0" in body


def _detail_client(tmp_path, monkeypatch, name):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client(), rid, iid


def test_v2_detail_shows_named_split_ui(tmp_path, monkeypatch):
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spv2.db")
    c.set_cookie("ui", "v2")
    try:
        body = c.get(f"/receipts/{rid}").get_data(as_text=True)
    finally:
        c.delete_cookie("ui")
    assert "Alice" in body and "Bob" in body
    assert f"share_{iid}_" in body
    assert "uid:share" not in body


def test_v1_detail_shows_named_split_ui(tmp_path, monkeypatch):
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spv1.db")
    body = c.get(f"/receipts/{rid}").get_data(as_text=True)
    assert "Alice" in body and "Bob" in body
    assert f"share_{iid}_" in body
    assert "uid:share" not in body


def test_named_share_fields_split(tmp_path, monkeypatch):
    import db as dbmod
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spn.db")
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "user_id": "1"},
           follow_redirects=True)
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(iid), "share_1": "0.5", "share_2": "0.5"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert sorted((r["user_id"], r["share"]) for r in rows) == [(1, 0.5), (2, 0.5)]


def test_zero_share_means_unassigned(tmp_path, monkeypatch):
    import db as dbmod
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spz.db")
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(iid), "share_1": "0", "share_2": "1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert [(r["user_id"], r["share"]) for r in rows] == [(2, 1.0)]


def test_zero_share_string_form(tmp_path, monkeypatch):
    import db as dbmod
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spzs.db")
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(iid), "shares": "1:0,2:1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert [(r["user_id"], r["share"]) for r in rows] == [(2, 1.0)]


def test_whole_assign_wins_over_prefilled_shares(tmp_path, monkeypatch):
    import db as dbmod
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spw.db")
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(iid), "share_1": "0.5", "share_2": "0.5"},
           follow_redirects=True)
    # UI prefills the boxes; picking "all → Bob" must win over them
    c.post(f"/receipts/{rid}/splits",
           data={"item_id": str(iid), "share_1": "0.5", "share_2": "0.5",
                 "user_id": "2"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert [(r["user_id"], r["share"]) for r in rows] == [(2, 1.0)]


def test_splits_success_redirect_targets_row(tmp_path, monkeypatch):
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spr.db")
    r = c.post(f"/receipts/{rid}/splits",
               data={"item_id": str(iid), "share_1": "0.5", "share_2": "0.5"},
               follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert r.headers["Location"].endswith(f"/receipts/{rid}#item-{iid}")


def test_splits_failure_redirect_has_no_fragment(tmp_path, monkeypatch):
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spf.db")
    r = c.post(f"/receipts/{rid}/splits",
               data={"item_id": str(iid), "share_1": "0.6", "share_2": "0.3"},
               follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert "#" not in r.headers["Location"]


def _slider_bodies(c, rid):
    bodies = [c.get(f"/receipts/{rid}").get_data(as_text=True)]
    c.set_cookie("ui", "v2")
    try:
        bodies.append(c.get(f"/receipts/{rid}").get_data(as_text=True))
    finally:
        c.delete_cookie("ui")
    return bodies


def test_two_user_row_shows_step10_slider(tmp_path, monkeypatch):
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spsl.db")
    for body in _slider_bodies(c, rid):
        assert 'type="range"' in body
        assert 'step="10"' in body
        assert "Alice" in body and "Bob" in body


def test_three_users_hide_slider(tmp_path, monkeypatch):
    import db as dbmod
    c, rid, iid = _detail_client(tmp_path, monkeypatch, "spsl3.db")
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Cara')")
    conn.commit()
    conn.close()
    for body in _slider_bodies(c, rid):
        assert 'type="range"' not in body
        assert "share_1" in body


def test_spending_counts_unsplit_items_equally(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "spend.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date, item_count, total) "
                 "VALUES ('s.txt','2026-09-05',1,10.0)")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='s.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    assert "Spending by user" in body
    assert body.count("<td>5.0</td>") == 2
