import db as dbmod


def test_init_db_adds_packaging_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "m.db"))
    dbmod.init_db()
    dbmod.init_db()  # idempotent re-run
    cols = [r[1] for r in dbmod.get_db().execute("PRAGMA table_info(products)").fetchall()]
    for c in ("purchase_qu_id", "qu_factor", "market", "needs_review"):
        assert c in cols


def test_units_table_seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    names = [r[0] for r in conn.execute("SELECT name FROM units WHERE source='local'").fetchall()]
    assert "Τεμάχιο" in names and "Γραμμάριο" in names


def test_grnorm_greek_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "g.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('ΑΛΦΑ','Άλφα γάλα')")
    rows = conn.execute("SELECT raw FROM products WHERE grnorm(clean) LIKE '%' || grnorm(?) || '%'", ("αλφα γαλα",)).fetchall()
    assert [r[0] for r in rows] == ["ΑΛΦΑ"]


KEPT_STUB = (
    "22 \u03a3\u03b5\u03c0 2026\nlogo\n"
    "TORTI.CHIPS SALT200G          2,78  13.00%\n"
    "    2 x          1,39      \n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               2,78\n"
    "0000   000000/00/00       01.01.26   12:00\n"
    "\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3 \u0394\u0395\u039a\u03a4\u0395\u03a3\n"
)


def test_import_creates_stub_products(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import fetch_lib as fl
    conn = dbmod.get_db()
    fl.import_kept_receipt(conn, KEPT_STUB, "26-09-22.txt", "TS")
    row = conn.execute("SELECT raw, needs_review FROM products WHERE raw LIKE 'TORTI%'").fetchone()
    assert row is not None and row["needs_review"] == 1
    conn.close()
