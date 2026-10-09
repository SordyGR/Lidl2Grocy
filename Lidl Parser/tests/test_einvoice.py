import pathlib

FIX = pathlib.Path(__file__).resolve().parent / "einvoice_lidl.html"


def _html():
    return FIX.read_text(encoding="utf-8")


def test_parse_lidl_invoice():
    import einvoice as E
    inv = E.parse_invoice(_html())
    assert inv["market"] == "Lidl"
    assert inv["date_iso"] == "2026-10-08"
    assert inv["doc_number"] == "000000-000000"
    assert inv["mark"] == "400000000000001"
    assert inv["total"] == 4.74
    assert len(inv["lines"]) == 1
    line = inv["lines"][0]
    assert line["name"] == "COLA 0% ΜΕ ΣΤΕΒΙΑ 2L"
    assert line["qty"] == 6.0
    assert line["unit_price"] == 0.79
    assert line["discount"] == 0.0
    assert line["vat"] == 13.0
    assert line["total"] == 4.74


def test_fetch_rejects_other_hosts():
    import einvoice as E
    try:
        E.fetch_invoice_html("https://evil.example/x")
    except ValueError:
        pass
    else:
        raise AssertionError("off-host URL must raise ValueError without any network")


def test_fetch_decodes_utf8_despite_headers(monkeypatch):
    import requests
    import einvoice as E

    class R:
        status_code = 200
        encoding = "ISO-8859-1"  # what requests guesses from headers
        content = "Ημ/νία έκδοσης: 08-10-2026 ΦΠΑ%".encode("utf-8")

        @property
        def text(self):
            return self.content.decode(self.encoding)

    monkeypatch.setattr(requests, "get", lambda url, **kw: R())
    out = E.fetch_invoice_html("https://e-invoicing.gr/api/GetInvoice?x=1")
    assert "Ημ/νία έκδοσης: 08-10-2026" in out


def test_fetch_resolves_viewer_url_to_api(monkeypatch):
    import requests
    import einvoice as E
    seen = {}

    class R:
        status_code = 200
        text = "<html>invoice</html>"

    def fake_get(url, **kw):
        seen["url"] = url
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    out = E.fetch_invoice_html(
        "https://e-invoicing.gr/edocuments/ViewInvoice/-1/11111111-2222-4333-8444-555555555555_aa0bb0cc0")
    assert out == "<html>invoice</html>"
    assert "/api/GetInvoice" in seen["url"]
    assert "intRefDocID=11111111-2222-4333-8444-555555555555" in seen["url"]
    assert "hashToken=aa0bb0cc0" in seen["url"]


def test_import_merges_into_catalog(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "ei.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES (?,?,?)",
                 ("COLA 0% ΜΕ ΣΤΕΒΙΑ 2L", "Cola", "Drinks"))
    conn.commit()
    conn.close()
    import einvoice as E
    inv = E.parse_invoice(_html())
    conn = dbmod.get_db()
    try:
        res = E.import_invoice(conn, inv, "26-10-08.txt")
        assert res["items"] == 1 and res["total"] == 4.74
        row = conn.execute("SELECT store, total, mark FROM receipts").fetchone()
        assert row["store"] == "Lidl" and row["total"] == 4.74
        assert row["mark"] == "400000000000001"
        item = conn.execute("SELECT clean FROM items").fetchone()
        assert item["clean"] == "Cola"  # shared catalog memory matched
        dup = E.import_invoice(conn, inv, "26-10-08.txt")
        assert dup.get("duplicate") is True
        n = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_route_imports_from_url(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eir.db"))
    dbmod.init_db()
    import app as appmod
    import einvoice as E
    html = _html()
    monkeypatch.setattr(E, "fetch_invoice_html", lambda url: html)
    appmod.app.testing = True
    body = appmod.app.test_client().post(
        "/fetch/einvoice", data={"url": "https://e-invoicing.gr/edocuments/x"},
        follow_redirects=True).get_data(as_text=True)
    assert "4.74" in body
    conn = dbmod.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    finally:
        conn.close()


def test_route_rejects_bad_host(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eir2.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().post(
        "/fetch/einvoice", data={"url": "https://evil.example/x"},
        follow_redirects=True).get_data(as_text=True)
    assert "e-invoicing.gr" in body
    conn = dbmod.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    finally:
        conn.close()


def test_dashboard_has_einvoice_box(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eib.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    assert "/fetch/einvoice" in c.get("/").get_data(as_text=True)
    c.set_cookie("ui", "v2")
    try:
        assert "/fetch/einvoice" in c.get("/").get_data(as_text=True)
    finally:
        c.delete_cookie("ui")


URL = "https://e-invoicing.gr/edocuments/ViewInvoice/-1/abc_def"


def test_import_stores_source_url(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eisrc.db"))
    dbmod.init_db()
    import einvoice as E
    conn = dbmod.get_db()
    try:
        E.import_invoice(conn, E.parse_invoice(_html()), "26-10-08.txt",
                         source_url=URL)
        row = conn.execute("SELECT source_url FROM receipts").fetchone()
        assert row["source_url"] == URL
    finally:
        conn.close()


def test_lidl_import_leaves_source_url_empty(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eisrc2.db"))
    dbmod.init_db()
    from fetch_lib import import_kept_receipt
    from test_fetch_import import KEPT
    conn = dbmod.get_db()
    try:
        import_kept_receipt(conn, KEPT, "26-09-22.txt", "T9")
        row = conn.execute("SELECT source_url FROM receipts").fetchone()
        assert row["source_url"] is None
    finally:
        conn.close()


def test_route_stores_source_url(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eisrc3.db"))
    dbmod.init_db()
    import app as appmod
    import einvoice as E
    html = _html()
    monkeypatch.setattr(E, "fetch_invoice_html", lambda url: html)
    appmod.app.testing = True
    appmod.app.test_client().post(
        "/fetch/einvoice", data={"url": URL}, follow_redirects=True)
    conn = dbmod.get_db()
    try:
        row = conn.execute("SELECT source_url FROM receipts").fetchone()
        assert row["source_url"] == URL
    finally:
        conn.close()


def test_detail_shows_source_link(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "eisrc4.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, source_url) "
                 "VALUES ('s.txt','2026-10-08',?)", (URL,))
    rid = conn.execute("SELECT id FROM receipts").fetchone()["id"]
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    bodies = [c.get(f"/receipts/{rid}").get_data(as_text=True)]
    c.set_cookie("ui", "v2")
    try:
        bodies.append(c.get(f"/receipts/{rid}").get_data(as_text=True))
    finally:
        c.delete_cookie("ui")
    for body in bodies:
        assert f'href="{URL}"' in body
