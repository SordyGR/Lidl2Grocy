import db as dbmod


def _client(tmp_path, monkeypatch, name="x.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _seed():
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, store, card_last4) VALUES ('26-09-22.txt','2026-09-22','Lidl','1236')")
    conn.execute("INSERT INTO receipts (filename, receipt_date, store) VALUES ('26-09-23.txt','2026-09-23','Lidl')")
    r1 = conn.execute("SELECT id FROM receipts WHERE filename='26-09-22.txt'").fetchone()[0]
    r2 = conn.execute("SELECT id FROM receipts WHERE filename='26-09-23.txt'").fetchone()[0]
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, vat, discount,
                    total, final, match_type, match_score)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (r1, "MILK 1L", "Milk", "Dairy", 1, 1.50, 13.0, 0.0, 1.50, 1.50, "exact", 100.0))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price, vat, discount,
                    total, final, match_type, match_score)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (r2, "MILK 1L", "Milk", "Dairy", 2, 1.60, 13.0, 0.0, 3.20, 3.20, "exact", 100.0))
    conn.execute("INSERT INTO products (raw, clean, category, barcode) VALUES ('MILK 1L','Milk','Dairy','123')")
    conn.commit()
    conn.close()


def _workbook(c):
    import io
    r = c.get("/export-analysis.xlsx")
    assert r.status_code == 200
    assert "spreadsheet" in r.headers.get("Content-Type", "")
    import pandas as pd
    return pd.read_excel(io.BytesIO(r.get_data()), sheet_name=None)


def test_sheets_columns_and_counts(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    wb = _workbook(c)
    assert set(wb) == {"All Data", "Price Trends"}
    all_data = wb["All Data"]
    assert len(all_data) == 2
    for col in ["Receipt", "Date", "Product", "Clean", "Category", "Qty", "Unit Price",
                "VAT", "Discount", "Final", "Match Type", "Match Score",
                "Barcode", "Store", "Card"]:
        assert col in all_data.columns, col
    assert all_data.iloc[0]["Date"] <= all_data.iloc[1]["Date"]  # date ordered
    import pandas as pd
    assert str(all_data.iloc[0]["Barcode"]) == "123"
    # NOTE: raw xlsx cells are text (verified via openpyxl: data_type 's',
    # so Excel keeps leading zeros); pd.read_excel infers float on read.
    assert str(int(all_data.iloc[0]["Card"])) == "1236"
    assert pd.isna(all_data.iloc[1]["Card"])  # empty stays empty, not "None"


def test_trends_match_hand_computation(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed()
    trends = _workbook(c)["Price Trends"]
    assert list(trends.columns) == ["Clean Product", "Date", "mean", "min", "max"]
    # pipeline2 groups by Clean × Date: one row per date, hand-checked values
    by_date = {str(r["Date"]): r for _, r in trends.iterrows()}
    assert abs(by_date["2026-09-22"]["mean"] - 1.50) < 1e-9
    assert abs(by_date["2026-09-23"]["mean"] - 1.60) < 1e-9
    assert abs(by_date["2026-09-23"]["max"] - 1.60) < 1e-9


def test_empty_db_gives_headers_only(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "e.db")
    wb = _workbook(c)
    assert len(wb["All Data"]) == 0 and len(wb["Price Trends"]) == 0
    assert "Clean" in wb["All Data"].columns
