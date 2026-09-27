"""beta-fixes: headed cooldown exemption, rc contract, entrypoint hygiene (RED first)."""
import os

import db as dbmod


def _client(tmp_path, monkeypatch, name="bf.db"):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / name))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    return appmod.app.test_client()


def test_headed_run_bypasses_login_cooldown(tmp_path, monkeypatch):
    import time
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync", lambda **kw: calls.append(kw) or 0)
    monkeypatch.setattr(appmod, "_LOGIN_COOLDOWN_UNTIL", time.time() + 1800, raising=False)
    # headless sync is refused during cooldown …
    r = c.post("/fetch/run")
    assert r.get_json()["started"] is False
    # … but the manual container-browser login (no Lidl traffic while
    # waiting) is exempt.
    r = c.post("/fetch/run", data={"headed": "1"})
    assert r.get_json()["started"] is True
    for _ in range(100):
        if not appmod._FETCH["running"]:
            break
        time.sleep(0.1)
    assert calls == [{"latest_only": False, "headed": True}]


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


def _fake_playwright(page):
    class FakeCtx:
        pages = [page]

        def add_init_script(self, *a, **k):
            pass

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, *a, **k):
            return FakeCtx()

    class FakePW:
        chromium = FakeChromium()

    class FakeSP:
        def __enter__(self):
            return FakePW()

        def __exit__(self, *a):
            return False

    return FakeSP


class _EmptyHistoryPage:
    url = "https://www.lidl-hellas.gr/mre/purchase-history?page=1"

    def goto(self, *a, **k):
        pass

    def wait_for_selector(self, *a, **k):
        raise Exception("no receipt links on page")

    def query_selector_all(self, *a, **k):
        return []

    def query_selector(self, *a, **k):
        return None


class _OneBadReceiptPage(_EmptyHistoryPage):
    def wait_for_selector(self, *a, **k):
        return None

    def query_selector_all(self, *a, **k):
        return [_El("https://x/mre/purchase-detail?t=bad1")]


def test_empty_history_is_success_not_error(tmp_path, monkeypatch):
    import fetch_lidl as fl
    import playwright.sync_api as sp
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setattr(fl, "PROFILE_DIR", str(tmp_path / "prof"))
    dbmod.init_db()
    monkeypatch.setattr(sp, "sync_playwright", lambda: _fake_playwright(_EmptyHistoryPage())())
    assert fl.cmd_sync() == 0


def test_partial_failure_reports_tids(tmp_path, monkeypatch):
    import fetch_lidl as fl
    import playwright.sync_api as sp
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(fl, "PROFILE_DIR", str(tmp_path / "prof"))
    dbmod.init_db()
    monkeypatch.setattr(sp, "sync_playwright", lambda: _fake_playwright(_OneBadReceiptPage())())
    assert fl.cmd_sync() == 4
    assert "bad1" in (fl.LAST_SYNC_ERROR or "")


def test_entrypoint_is_lf_and_gitattributes_pins_sh(tmp_path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "docker-entrypoint.sh"), "rb") as f:
        data = f.read()
    assert b"\r" not in data, "docker-entrypoint.sh must be LF-only (sh chokes on CR)"
    root = os.path.dirname(os.path.dirname(here))
    attrs = os.path.join(root, ".gitattributes")
    assert os.path.exists(attrs)
    assert "*.sh text eol=lf" in open(attrs, encoding="utf-8").read()
