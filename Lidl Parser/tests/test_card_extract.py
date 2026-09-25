def test_extracts_masked_last4():
    from receipt_parser import extract_card_last4
    assert extract_card_last4("xxx\n  ************9366  \n") == "9366"
    assert extract_card_last4("  46751465****9366  \n") == "9366"
    assert extract_card_last4("no card here\n") is None


def test_tables_exist(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "card_last4" in cols
    assert {"users", "card_map", "item_splits"} <= tables
