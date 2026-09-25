def test_migrate_no_legacy_files(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "fresh.db"))
    import migrate as m
    monkeypatch.setattr(m, "PRODUCT_MAP", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(m, "HISTORY_CSV", str(tmp_path / "nope2.csv"))
    monkeypatch.setattr(m, "BARCODES_XLSX", str(tmp_path / "nope.xlsx"))
    monkeypatch.setattr(m, "RECEIPT_FOLDER", str(tmp_path / "no-receipts-dir"))
    m.migrate()
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    conn.close()
    assert n == 0
