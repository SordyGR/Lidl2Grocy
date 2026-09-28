import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "ext.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_token_autogenerates_and_persists(tmp_path, monkeypatch):
    import app as appmod
    _client(tmp_path, monkeypatch)
    t1 = appmod._ext_token()
    assert len(t1) >= 32
    assert appmod._ext_token() == t1  # second call reads DB, no regen


def test_auth_gate(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    assert c.get("/api/ext/status").status_code == 401
    assert c.get("/api/ext/status",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/ext/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert tok not in r.get_data(as_text=True)


def _authed(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_need_diffs_against_fetch_state(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO fetch_state (t_id, filename) VALUES (?, ?)", ("old1", "f.txt"))
    conn.commit()
    conn.close()
    r = c.post("/api/ext/need", json={"tids": ["old1", "new1", "new1"]}, headers=_authed(tok))
    assert r.status_code == 200
    assert r.get_json() == {"new": ["new1"]}


def test_need_rejects_garbage(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(tok)
    assert c.post("/api/ext/need", json={"tids": "nope"}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={"tids": ["x"] * 5001}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={"tids": ["a"]}).status_code == 401


def test_receipt_import_duplicate_and_422(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(tok)
    assert c.post("/api/ext/receipt", json={"tid": "t1"}, headers=h).status_code == 400
    # forced parse failure must not record state (retry later)
    monkeypatch.setattr("fetch_lib.trim_receipt_text",
                        lambda text: (_ for _ in ()).throw(ValueError("no date")))
    r = c.post("/api/ext/receipt", json={"tid": "bad1", "text": "garbage"}, headers=h)
    assert r.status_code == 422
    conn = dbmod.get_db()
    assert conn.execute("SELECT 1 FROM fetch_state WHERE t_id='bad1'").fetchone() is None
    conn.close()


def test_receipt_end_to_end_with_fixture(tmp_path, monkeypatch):
    from test_fetch_import import KEPT
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(tok)
    r1 = c.post("/api/ext/receipt", json={"tid": "e2e1", "text": KEPT}, headers=h)
    assert r1.get_json()["status"] == "imported"
    r2 = c.post("/api/ext/receipt", json={"tid": "e2e1", "text": KEPT}, headers=h)
    assert r2.get_json()["status"] == "duplicate"
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) AS c FROM receipts").fetchone()["c"] == 1
    conn.close()
    s = c.get("/api/ext/status", headers=h).get_json()
    assert s["ok"] is True and s["receipts"] == 1 and s["last_sync"] is not None


def test_paste_route(tmp_path, monkeypatch):
    import os
    _client(tmp_path, monkeypatch)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    src = os.path.join(os.path.dirname(appmod.__file__), "tests", "data_paste.txt")
    assert os.path.exists(src), "fixture missing"
    with open(src, encoding="utf-8") as f:
        text = f.read()
    r = c.post("/fetch/paste", data={"text": text, "tid": "test-1"}, follow_redirects=True)
    assert r.status_code == 200
