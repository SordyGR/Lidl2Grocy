def test_same_day_receipts_order_by_seq(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "o.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, receipt_seq) VALUES ('26-09-05.1.txt','2026-09-05',2)")
    conn.execute("INSERT INTO receipts (filename, receipt_date, receipt_seq) VALUES ('26-09-05.txt','2026-09-05',1)")
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/receipts").get_data(as_text=True)
    assert body.index("26-09-05.txt") < body.index("26-09-05.1.txt")
