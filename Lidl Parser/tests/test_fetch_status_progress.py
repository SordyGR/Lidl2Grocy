"""fetch_status exposes backfill progress (RED first)."""
import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def test_status_includes_progress(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    conn = dbmod.get_db()
    try:
        dbmod.set_sync_state(conn, phase="backfill", last_page=4,
                             last_tid="t9", new_count=120, skipped_count=30)
    finally:
        conn.close()
    j = c.get("/api/fetch_status").get_json()
    assert j["phase"] == "backfill"
    assert j["last_page"] == 4
    assert j["new_count"] == 120
    assert j["skipped_count"] == 30
