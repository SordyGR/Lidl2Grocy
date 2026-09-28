"""sync_state cursor round-trip (RED first)."""
import db as dbmod


def test_sync_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    try:
        st = dbmod.get_sync_state(conn)
        assert st["last_page"] == 0
        dbmod.set_sync_state(conn, phase="backfill", last_page=4,
                             last_tid="abc", new_count=120, skipped_count=30)
        st = dbmod.get_sync_state(conn)
        assert st["phase"] == "backfill"
        assert st["last_page"] == 4
        assert st["last_tid"] == "abc"
        assert st["new_count"] == 120
        assert st["skipped_count"] == 30
        assert st["updated_at"]
    finally:
        conn.close()
