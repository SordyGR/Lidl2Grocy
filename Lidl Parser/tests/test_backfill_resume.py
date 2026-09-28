"""Backfill resume: skip seen, fetch unseen, advance cursor, early-stop (RED first)."""
import re

import db as dbmod


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


class _FakePage:
    """History pages from tids_by_page; detail bodies from bodies."""

    def __init__(self, tids_by_page, bodies):
        self._tids = tids_by_page
        self._bodies = bodies
        self.url = "https://www.lidl-hellas.gr/mre/purchase-history?page=1"
        self._cur = 1
        self.detail_calls = []

    def goto(self, url):
        self.url = url
        m = re.search(r"page=(\d+)", url)
        if m:
            self._cur = int(m.group(1))

    def wait_for_selector(self, sel, timeout=15000):
        if "purchase-detail" in sel and not self._tids.get(self._cur):
            raise Exception("no links")

    def query_selector_all(self, sel):
        return [_El(f"https://x/mre/purchase-detail?t={t}")
                for t in self._tids.get(self._cur, [])]

    def query_selector(self, sel):
        return None

    def inner_text(self, sel):
        return ""


class _FakeCtx:
    def __init__(self, page):
        self.pages = [page]
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        pass


def test_resume_skips_seen_and_advances_cursor(tmp_path, monkeypatch):
    import fetch_lidl as fl
    import playwright.sync_api as sp
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "r.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
                 ("old1", "26-01-01.txt"))
    conn.commit()
    conn.close()

    tids_by_page = {1: ["new1", "old1"], 2: []}
    bodies = {"new1": "body-new1"}
    page = _FakePage(tids_by_page, bodies)

    class _PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def chromium(self):
            class _C:
                def launch_persistent_context(inner_self, *a, **k):
                    return _FakeCtx(page)
            return _C()

    import contextlib

    @contextlib.contextmanager
    def _fake_pw():
        yield _PW()

    monkeypatch.setattr(sp, "sync_playwright", _fake_pw)
    monkeypatch.setattr(fl, "launch_context", lambda p, headed=False: _FakeCtx(page))
    monkeypatch.setattr(fl, "fetch_detail_text", lambda p, tid: bodies[tid])
    monkeypatch.setattr(fl, "trim_receipt_text", lambda t: t)
    monkeypatch.setattr(fl, "receipt_body_date", lambda t: ("26", "01", "01"))
    names = iter(["26-01-01.1.txt", "26-01-01.2.txt"])
    monkeypatch.setattr(fl, "pick_filename", lambda d, c, *a: next(names))
    monkeypatch.setattr(
        fl, "import_kept_receipt",
        lambda conn, kept, name, tid: conn.execute(
            "INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
            (tid, name)))

    rc = fl.cmd_sync(early_stop=10)
    assert rc == 0
    conn = dbmod.get_db()
    try:
        seen = {r[0] for r in conn.execute("SELECT t_id FROM fetch_state").fetchall()}
        assert {"old1", "new1"} <= seen
        st = dbmod.get_sync_state(conn)
        assert st["last_tid"] == "old1"  # last processed (skipped) tid on page 1
        assert st["last_page"] == 1
        assert st["new_count"] == 1
        assert st["skipped_count"] == 1
    finally:
        conn.close()


def test_incremental_early_stop_avoids_detail_fetches(tmp_path, monkeypatch):
    import fetch_lidl as fl
    import playwright.sync_api as sp
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "e.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    for t in ("s1", "s2", "s3"):
        conn.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
                     (t, f"{t}.txt"))
    conn.commit()
    conn.close()

    tids_by_page = {1: ["s1", "s2", "s3"], 2: ["s4"]}
    page = _FakePage(tids_by_page, {})

    class _PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def chromium(self):
            class _C:
                def launch_persistent_context(inner_self, *a, **k):
                    return _FakeCtx(page)
            return _C()

    import contextlib

    @contextlib.contextmanager
    def _fake_pw():
        yield _PW()

    monkeypatch.setattr(sp, "sync_playwright", _fake_pw)
    monkeypatch.setattr(fl, "launch_context", lambda p, headed=False: _FakeCtx(page))
    calls = []
    monkeypatch.setattr(fl, "fetch_detail_text",
                        lambda p, tid: calls.append(tid) or "body")

    rc = fl.cmd_sync(early_stop=2)
    assert rc == 0
    assert calls == []  # stopped after 2 consecutive seen, never fetched
