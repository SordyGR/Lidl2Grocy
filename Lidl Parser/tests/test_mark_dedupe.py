import db as dbmod

FULL = (
    "22 \u03a3\u03b5\u03c0 2026\nlogo\n"
    "TORTI.CHIPS SALT200G          2,78  13.00%\n"
    "    2 x          1,39      \n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               2,78\n"
    "0000   000000/00/00       01.01.26   12:00\n"
    "\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3 \u0394\u0395\u039a\u03a4\u0395\u03a3\n"
    "\u03a6\u039f\u03a1\u039f\u039b\u039f\u0393\u0399\u039a\u0397 \u0391\u03a0\u039f\u0394\u0395\u0399\u039e\u0397 \u039b\u0397\u039e\u0397\n"
    "2020_07_102Entersoft_001_Paperless\n"
    "MAPK: 400000000000001\n"
    "UID\n"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
)


def _client(tmp_path, monkeypatch, name="k.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def _auth(monkeypatch):
    import settings_store as ss
    monkeypatch.setenv("EXT_INGEST_TOKEN", "t")
    return {"Authorization": "Bearer t"}


def test_extract_mark_finds_mapk():
    from fetch_lib import extract_mark
    assert extract_mark(FULL) == "400000000000001"


def test_import_same_mark_different_tid_is_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "k2.db"))
    dbmod.init_db()
    from fetch_lib import import_kept_receipt, trim_receipt_text
    conn = dbmod.get_db()
    kept = trim_receipt_text(FULL)
    r1 = import_kept_receipt(conn, kept, "26-09-22.txt", "T1", mark="400000000000001")
    assert r1.get("duplicate") is not True
    r2 = import_kept_receipt(conn, kept, "26-09-23.txt", "T2", mark="400000000000001")
    assert r2.get("duplicate") is True
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    conn.close()


def test_ext_receipt_same_mark_different_tid_is_duplicate(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch, "k3.db")
    h = _auth(monkeypatch)
    r1 = c.post("/api/ext/receipt", json={"tid": "AAA", "text": FULL}, headers=h)
    assert r1.get_json()["status"] == "imported"
    r2 = c.post("/api/ext/receipt", json={"tid": "BBB", "text": FULL}, headers=h)
    assert r2.get_json()["status"] == "duplicate"
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    conn.close()
    import app as appmod
    import os as _os
    import glob as _glob
    saved = _glob.glob(_os.path.join(appmod.RECEIPT_DIR, "*.txt"))
    assert saved and any("MAPK: 400000000000001" in open(p, encoding="utf-8").read() for p in saved)
