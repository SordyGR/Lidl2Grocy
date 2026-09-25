# Lidl Parser/tests/test_ui.py — theme, mappings sort/filter, bulk edit.
def test_theme_toggle_sets_cookie():
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/theme/dark", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "theme=dark" in r.headers.get("Set-Cookie", "")
    r2 = c.get("/")
    assert 'data-theme="dark"' in r2.get_data(as_text=True)


def test_theme_defaults_to_system():
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/")
    assert 'data-theme="system"' in r.get_data(as_text=True)


def test_theme_rejects_bad_mode():
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/theme/neon", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "theme=" not in r.headers.get("Set-Cookie", "")


def _seed_mappings(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category, barcode, grocy_id, grocy_track) VALUES ('RB','Banana','Food','111',7,1)")
    conn.execute("INSERT INTO products (raw, clean, category, barcode, grocy_track) VALUES ('RA','Apple','Food','222',1)")
    conn.execute("INSERT INTO products (raw, clean, category, grocy_track) VALUES ('RC','Soap','Household',0)")
    conn.commit()
    conn.close()


def test_mappings_sorts_by_clean(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    _seed_mappings(dbmod)
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    appmod.app.testing = True
    c = appmod.app.test_client()
    asc = c.get("/mappings?sort=clean&dir=asc").get_data(as_text=True)
    desc = c.get("/mappings?sort=clean&dir=desc").get_data(as_text=True)
    assert asc.index('value="RA"') < asc.index('value="RB"') < asc.index('value="RC"')
    assert desc.index('value="RC"') < desc.index('value="RB"') < desc.index('value="RA"')


def test_mappings_rejects_bad_sort(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s2.db"))
    dbmod.init_db()
    _seed_mappings(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/mappings?sort=grocy_track;DROP&dir=sideways")
    assert r.status_code == 200
    assert "Apple" in r.get_data(as_text=True)


def test_mappings_filters(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "f.db"))
    dbmod.init_db()
    _seed_mappings(dbmod)
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.delenv("GROCY_URL", raising=False)
    monkeypatch.delenv("GROCY_API_KEY", raising=False)
    appmod.app.testing = True
    c = appmod.app.test_client()
    cat = c.get("/mappings?cat=Food").get_data(as_text=True)
    assert 'value="RA"' in cat and 'value="RC"' not in cat
    linked = c.get("/mappings?grocy=linked").get_data(as_text=True)
    assert 'value="RB"' in linked and 'value="RA"' not in linked
    unlinked = c.get("/mappings?grocy=unlinked").get_data(as_text=True)
    assert 'value="RA"' in unlinked and 'value="RB"' not in unlinked
    skipped = c.get("/mappings?track=skipped").get_data(as_text=True)
    assert 'value="RC"' in skipped and 'value="RA"' not in skipped


def _seed_bulk(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('R1','Old 1','Misc')")
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('R2','Old 2','Misc')")
    conn.execute("INSERT INTO products (raw, clean, category) VALUES ('R3','Old 3','Misc')")
    conn.commit()
    conn.close()


def test_bulk_update_saves_checked_only(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "b.db"))
    dbmod.init_db()
    _seed_bulk(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/mappings/bulk-update", data={
        "count": "2",
        "raw_0": "R1", "clean_0": "New 1", "category_0": "Dairy",
        "barcode_0": "", "brand_0": "", "unit_0": "", "notes_0": "",
        "raw_1": "R2", "clean_1": "New 2", "category_1": "__new__", "new_category_1": "Bakery",
        "barcode_1": "", "brand_1": "", "unit_1": "", "notes_1": "",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Updated 2 mapping(s)" in r.get_data(as_text=True)
    conn = dbmod.get_db()
    rows = {x["raw"]: dict(x) for x in conn.execute("SELECT raw, clean, category FROM products").fetchall()}
    conn.close()
    assert rows["R1"]["clean"] == "New 1" and rows["R1"]["category"] == "Dairy"
    assert rows["R2"]["clean"] == "New 2" and rows["R2"]["category"] == "Bakery"
    assert rows["R3"]["clean"] == "Old 3"


def test_bulk_update_empty_selection(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "b2.db"))
    dbmod.init_db()
    _seed_bulk(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/mappings/bulk-update", data={"count": "0"}, follow_redirects=True)
    assert "Updated 0 mapping(s)" in r.get_data(as_text=True)
