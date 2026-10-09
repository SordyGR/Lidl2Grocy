import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "x.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, card_last4) VALUES ('26-09-01.txt','2026-09-01','1234')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='26-09-01.txt'").fetchone()["id"]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, discount, total, final)
                    VALUES (?, 'ΓΑΛΑ','Γάλα','Dairy',2,1.50,0,3.00,3.00)""", (rid,))
    iid = conn.execute("SELECT id FROM items WHERE raw='ΓΑΛΑ'").fetchone()["id"]
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    uid = conn.execute("SELECT id FROM users WHERE name='Alice'").fetchone()["id"]
    aid = conn.execute("SELECT id FROM users WHERE name='Bob'").fetchone()["id"]
    conn.execute("INSERT INTO card_map (last4, user_id) VALUES ('1234', ?)", (uid,))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,0.5)", (iid, uid))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,0.5)", (iid, aid))
    conn.execute("INSERT INTO grocy_pushes (receipt_id, pushed_count, skipped_count, errors) VALUES (?,1,0,'[]')", (rid,))
    conn.commit()
    conn.close()


def test_export_csv_content(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    r = c.get("/export.csv?from=2026-09-01&to=2026-09-30")
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert text.startswith("﻿")
    lines = text.splitlines()
    assert lines[0].lstrip("﻿") == "receipt_date,filename,store,raw,clean,category,qty,unit,unit_price,discount,final,card_last4,payer,splits,grocy_status"
    assert "Γάλα" in lines[1] and "Alice" in lines[1] and "Bob:0.5" in lines[1] and "pushed" in lines[1]
    assert "attachment" in r.headers.get("Content-Disposition", "")


def test_export_defaults_and_bad_dates(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    assert c.get("/export.csv").status_code == 200
    assert c.get("/export.csv?from=bogus").status_code == 400


def test_export_blank_payer_and_splits(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('26-09-02.txt','2026-09-02')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='26-09-02.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, qty, unit_price, total, final) VALUES (?, 'X','X',1,1.0,1.0,1.0)", (rid,))
    conn.commit()
    conn.close()
    r = c.get("/export.csv?from=2026-09-02&to=2026-09-02")
    assert r.status_code == 200
    assert "unpushed" in r.get_data(as_text=True)
