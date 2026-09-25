import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fetch_lidl import is_login_redirect, wait_for_login

def test_login_redirect_detection():
    assert is_login_redirect("https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/")
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
    page = _StubPage(["https://www.lidl-hellas.gr/mre/purchase-history?x=1"])
    assert wait_for_login(page, timeout_s=5) is True

def test_wait_for_login_times_out():
    page = _StubPage(["https://www.lidl-hellas.gr/user-api/login?step=login"] * 100)
    assert wait_for_login(page, timeout_s=0) is False
