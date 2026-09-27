"""Web-popup Lidl login + two-step auto-login (RED first)."""


class _FakePage:
    """Email first, password only after E-next click. Submit-step clicks hit
    a decoy (header back button) and do nothing; only in-field Enter submits."""

    def __init__(self):
        self.url = "https://www.lidl-hellas.gr/user-api/login?step=login"
        self.advanced = False
        self.filled = {}
        self.clicked = []
        self.submit_clicks = 0

    def goto(self, url):
        self.url = url

    def fill(self, sel, value, timeout=5000):
        if "password" in sel and not self.advanced:
            raise Exception("password not visible yet")
        self.filled[sel] = value

    def click(self, sel, timeout=5000):
        self.clicked.append(sel)
        if "password" in sel:
            self.submit_clicks += 1
            if any("password" in s for s in self.filled):
                self.url = "https://www.lidl-hellas.gr/"
        elif not self.advanced:
            self.advanced = True
            self.url = "https://www.lidl-hellas.gr/user-api/login?step=password"
        else:
            self.submit_clicks += 1
            if any("password" in s for s in self.filled):
                self.url = "https://www.lidl-hellas.gr/"

    def wait_for_selector(self, *a, **k):
        return None

    def wait_for_timeout(self, ms):
        return None

    def inner_text(self, sel):
        return ""

    def query_selector_all(self, *a, **k):
        raise Exception("no direct matches")

    def press(self, sel, key, timeout=None):
        if "password" in sel and any("password" in s for s in self.filled):
            self.url = "https://www.lidl-hellas.gr/"

    def press_enter(self):
        if (self.advanced and any("password" in s for s in self.filled)
                and not self.submit_clicks):
            self.url = "https://www.lidl-hellas.gr/"


class _Kb:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.press_enter()


def _two_step_page():
    p = _FakePage()
    p.keyboard = _Kb(p)
    p.wait_for_timeout = lambda ms: None
    return p


def test_auto_login_handles_two_step(monkeypatch):
    import fetch_lidl as fl
    monkeypatch.setattr(fl, 'LAST_LOGIN_NOTE', None, raising=False)
    monkeypatch.setattr(fl, "get_lidl_creds", lambda: ("u@example.com", "secret"))
    page = _two_step_page()
    assert fl.try_auto_login(page) is True
    assert page.advanced is True
    assert any("password" in s for s in page.filled)


def _client(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "weblogin.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_login_popup_renders_unbranded_form(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/fetch/login")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'name="email"' in body
    assert 'name="password"' in body
    # auto-login form stays unbranded, but the popup must offer the real
    # Lidl site for manual login + a paste fallback
    assert "lidl-hellas.gr" in body
    assert "/fetch/paste" in body


def test_login_popup_post_success_saves_and_reports(tmp_path, monkeypatch):
    import app as appmod
    import fetch_lidl as fl
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(fl, "attempt_login", lambda email, pw: True)
    r = c.post("/fetch/login", data={"email": "u@example.com", "password": "secret", "save": "on"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    import settings_store as ss
    assert ss.get("LIDL_USER") == "u@example.com"
    import secret_store as enc
    assert enc.get_secret("LIDL_PASS") == "secret"
    assert ss.get("LIDL_PASS").startswith("enc$")


def test_login_popup_post_failure_returns_401(tmp_path, monkeypatch):
    import fetch_lidl as fl
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(fl, "attempt_login", lambda email, pw: False)
    r = c.post("/fetch/login", data={"email": "u@example.com", "password": "wrong"})
    assert r.status_code == 401
    assert r.get_json()["ok"] is False


def test_dashboard_wires_container_browser_login(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    body = c.get("/").get_data(as_text=True)
    assert 'id="login-btn"' in body
    # The login button opens the container's persistent browser viewer, then
    # starts a headed sync so the user's Lidl session is stored in Docker.
    assert "/browser/vnc.html?autoconnect=true" in body
    assert "path=%2Fwebsockify" in body
    assert "headed: '1'" in body


def test_login_rejected_while_sync_running(tmp_path, monkeypatch):
    import threading
    import time
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "busy.db"))
    dbmod.init_db()
    import app as appmod
    entered = threading.Event()
    release = threading.Event()

    def blocked_sync(**kw):
        entered.set()
        assert release.wait(timeout=10)
        return 0

    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync", blocked_sync)
    import fetch_lidl as fl

    def _must_not_run(email, pw):
        raise AssertionError("attempt_login must not run during sync")

    monkeypatch.setattr(fl, "attempt_login", _must_not_run)
    appmod.app.testing = True
    c = appmod.app.test_client()
    t = threading.Thread(target=lambda: c.post("/fetch/run"))
    t.start()
    assert entered.wait(timeout=10)
    r = c.post("/fetch/login", data={"email": "u@example.com", "password": "secret"})
    assert r.status_code == 409
    assert r.get_json()["ok"] is False
    release.set()
    t.join(timeout=10)
    for _ in range(100):
        if not appmod._FETCH["running"]:
            break
        time.sleep(0.1)


def test_sync_rejected_while_login_running(tmp_path, monkeypatch):
    import threading
    import fetch_lidl as fl
    c = _client(tmp_path, monkeypatch)
    import app as appmod
    entered = threading.Event()
    release = threading.Event()

    def blocked_login(email, pw):
        entered.set()
        assert release.wait(timeout=10)
        return True

    monkeypatch.setattr(fl, "attempt_login", blocked_login)
    results = {}

    def first():
        results["r"] = c.post("/fetch/login",
                              data={"email": "u@example.com", "password": "secret"})

    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(timeout=10)
    r = c.post("/fetch/run")
    assert r.get_json()["started"] is False
    assert "login" in r.get_json()["reason"].lower()
    release.set()
    t.join(timeout=10)
    assert results["r"].get_json()["ok"] is True


def test_launch_hides_automation_flag():
    from playwright.sync_api import sync_playwright
    import fetch_lidl as fl
    with sync_playwright() as p:
        ctx = fl.launch_context(p, headed=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            fp = page.evaluate("({webdriver: navigator.webdriver})")
        finally:
            ctx.close()
    assert fp["webdriver"] in (None, False)


class _OverloadPage(_FakePage):
    def click(self, sel, timeout=5000):
        # Lidl swallows every submit under throttling; page never leaves
        self.clicked.append(sel)
        if "password" not in sel and not self.advanced:
            self.advanced = True
            self.url = "https://www.lidl-hellas.gr/user-api/login?step=password"
        else:
            self.submit_clicks += 1

    def press(self, sel, key, timeout=None):
        pass  # Lidl swallows the submit under throttling; page never leaves

    def inner_text(self, sel):
        return "Υπερπληρότητα συστήματος Παρακαλούμε δοκίμασε ξανά"


def test_overload_banner_sets_note(monkeypatch):
    import fetch_lidl as fl
    monkeypatch.setattr(fl, 'LAST_LOGIN_NOTE', None, raising=False)
    monkeypatch.setattr(fl, "get_lidl_creds", lambda: ("u@example.com", "secret"))
    page = _OverloadPage()
    page.keyboard = _Kb(page)
    page.wait_for_timeout = lambda ms: None
    assert fl.try_auto_login(page) is False
    assert getattr(fl, "LAST_LOGIN_NOTE", None) == "overload"


def test_login_post_reports_overload_once_then_cools_down(tmp_path, monkeypatch):
    import time
    import fetch_lidl as fl
    import app as appmod
    c = _client(tmp_path, monkeypatch)

    def failed_overload(email, pw):
        monkeypatch.setattr(fl, "LAST_LOGIN_NOTE", "overload")
        return False

    monkeypatch.setattr(fl, "attempt_login", failed_overload)
    monkeypatch.setattr(appmod, "_LOGIN_COOLDOWN_UNTIL", 0.0, raising=False)
    r = c.post("/fetch/login", data={"email": "u@example.com", "password": "secret"})
    assert r.status_code == 429
    assert "wait" in r.get_json()["error"].lower()

    def must_not_run(email, pw):
        raise AssertionError("attempt must not run during cooldown")

    monkeypatch.setattr(fl, "attempt_login", must_not_run)
    r2 = c.post("/fetch/login", data={"email": "u@example.com", "password": "secret"})
    assert r2.status_code == 429
    assert time.time() < appmod._LOGIN_COOLDOWN_UNTIL


def test_classify_failure_notes():
    import fetch_lidl as fl
    assert fl._classify_failure("Υπερπληρότητα συστήματος") == "overload"
    assert fl._classify_failure("System Overload, retry") == "overload"
    assert fl._classify_failure("Συνέχεια σε δημιουργία λογαριασμού") == "no-account"
    assert fl._classify_failure("Λάθος κωδικός, δοκιμάστε ξανά") == "bad-password"
    assert fl._classify_failure("some unrelated page") is None


def test_submit_uses_blue_button_not_header(monkeypatch):
    import fetch_lidl as fl
    monkeypatch.setattr(fl, 'LAST_LOGIN_NOTE', None, raising=False)
    monkeypatch.setattr(fl, "get_lidl_creds", lambda: ("u@example.com", "secret"))
    page = _two_step_page()
    assert fl.try_auto_login(page) is True
    blue = [s for s in page.clicked if 'type="button"' in s]
    assert blue, "submit must target the type=button form button"


def test_unknown_email_reports_no_account(monkeypatch):
    import fetch_lidl as fl
    monkeypatch.setattr(fl, 'LAST_LOGIN_NOTE', None, raising=False)
    monkeypatch.setattr(fl, "get_lidl_creds", lambda: ("u@example.com", "secret"))

    class _NoPassword(_FakePage):
        def wait_for_selector(self, *a, **k):
            raise Exception("still email stage")

        def inner_text(self, sel):
            return "Συνέχεια σε δημιουργία λογαριασμού"

    assert fl.try_auto_login(_NoPassword()) is False
    assert fl.LAST_LOGIN_NOTE == "no-account"


def test_login_post_reports_bad_password(tmp_path, monkeypatch):
    import fetch_lidl as fl
    import app as appmod
    c = _client(tmp_path, monkeypatch)

    def failed_bad_pw(email, pw):
        monkeypatch.setattr(fl, "LAST_LOGIN_NOTE", "bad-password")
        return False

    monkeypatch.setattr(fl, "attempt_login", failed_bad_pw)
    monkeypatch.setattr(appmod, "_LOGIN_COOLDOWN_UNTIL", 0.0, raising=False)
    r = c.post("/fetch/login", data={"email": "u@example.com", "password": "secret"})
    assert r.status_code == 401
    assert "password" in r.get_json()["error"].lower()
