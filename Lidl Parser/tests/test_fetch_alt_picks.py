"""Cherry-picks from the LIDL-ReceiptCatcher alternative (RED first)."""


def test_launch_has_docker_flags_and_env_profile_dir(tmp_path, monkeypatch):
    import fetch_lidl as fl
    seen = {}

    class FakeCtx:
        pages = []

        def add_init_script(self, *a, **k):
            pass

        def new_page(self):
            raise AssertionError("no page needed")

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kw):
            seen["profile_dir"] = profile_dir
            seen["kwargs"] = kw
            return FakeCtx()

    class FakePW:
        chromium = FakeChromium()

    custom = str(tmp_path / "prof")
    monkeypatch.setenv("LIDL_PROFILE_DIR", custom)
    import importlib
    importlib.reload(fl)
    try:
        fl.launch_context(FakePW(), headed=False)
    finally:
        monkeypatch.delenv("LIDL_PROFILE_DIR", raising=False)
        importlib.reload(fl)
    assert seen["profile_dir"] == custom
    assert "--no-sandbox" in seen["kwargs"].get("args", [])


def test_login_redirect_case_insensitive():
    import fetch_lidl as fl
    assert fl.is_login_redirect("https://www.lidl-hellas.gr/User-Api/Login?step=login") is True
    assert fl.is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-history?page=1") is False


def test_collect_tids_accepts_alphanumeric():
    import fetch_lidl as fl

    class El:
        def __init__(self, href):
            self._href = href

        def get_attribute(self, _):
            return self._href

    class Page:
        url = "https://www.lidl-hellas.gr/mre/purchase-history?page=1"

        def goto(self, *a, **k):
            pass

        def wait_for_selector(self, *a, **k):
            pass

        def query_selector_all(self, *a, **k):
            return [El("https://x/mre/purchase-detail?t=abc-123_X"),
                    El("https://x/mre/purchase-detail?t=456")]

        def query_selector(self, *a, **k):
            return None

    assert fl.collect_tids(Page()) == ["abc-123_X", "456"]
