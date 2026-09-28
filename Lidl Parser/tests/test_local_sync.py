"""Local headless-Chromium sync (CLI only; no Playwright browser needed for tests)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_login_redirect_detection():
    from fetch_lidl import is_login_redirect
    assert is_login_redirect("https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/")
    assert is_login_redirect("https://www.lidl-hellas.gr/User-Api/Login?step=login") is True
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=1")
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-detail?client_id=GreeceRetailClient&t=14000")


class _StubPage:
    def __init__(self, urls):
        self._urls = list(urls)

    @property
    def url(self):
        if len(self._urls) > 1:
            return self._urls.pop(0)
        return self._urls[0]


def test_wait_for_login_already_logged_in():
    from fetch_lidl import wait_for_login
    page = _StubPage(["https://www.lidl-hellas.gr/mre/purchase-history?x=1"])
    assert wait_for_login(page, timeout_s=5) is True


def test_wait_for_login_times_out():
    from fetch_lidl import wait_for_login
    page = _StubPage(["https://www.lidl-hellas.gr/user-api/login?step=login"] * 100)
    assert wait_for_login(page, timeout_s=0) is False


def test_collect_tids_accepts_alphanumeric():
    from fetch_lidl import collect_tids

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

    assert collect_tids(Page()) == ["abc-123_X", "456"]


def test_classify_failure_notes():
    from fetch_lidl import _classify_failure
    assert _classify_failure("Υπερπληρότητα συστήματος") == "overload"
    assert _classify_failure("System Overload, retry") == "overload"
    assert _classify_failure("Λάθος κωδικός, δοκιμάστε ξανά") == "bad-password"
    assert _classify_failure("some unrelated page") is None
