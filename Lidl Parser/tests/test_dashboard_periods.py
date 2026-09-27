import db as dbmod
from datetime import date, timedelta


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "d.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    today = date.today()
    old = str(today - timedelta(days=200))
    new = str(today - timedelta(days=2))
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('old.txt', ?)", (old,))
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('new.txt', ?)", (new,))
    ro = conn.execute("SELECT id FROM receipts WHERE filename='old.txt'").fetchone()["id"]
    rn = conn.execute("SELECT id FROM receipts WHERE filename='new.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final) VALUES (?, 'O','Oldcat','Vintage',1,9.0,9.0,9.0)", (ro,))
    conn.execute("INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, total, final) VALUES (?, 'N','Newcat','Fresh',1,1.0,1.0,1.0)", (rn,))
    conn.commit()
    conn.close()


def test_pie_period_filters(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    all_body = c.get("/").get_data(as_text=True)
    assert "Vintage" in all_body and "Fresh" in all_body
    month = c.get("/?pie=30d").get_data(as_text=True)
    assert "Vintage" not in month and "Fresh" in month


def test_pie_custom_and_bogus(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    today = date.today()
    frm = str(today - timedelta(days=300))
    custom = c.get(f"/?pie=custom&from={frm}&to={today}").get_data(as_text=True)
    assert "Vintage" in custom and "Fresh" in custom
    bogus = c.get("/?pie=bogus").get_data(as_text=True)
    assert "Vintage" in bogus and "Fresh" in bogus
