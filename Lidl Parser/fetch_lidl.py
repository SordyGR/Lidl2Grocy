"""Fetch Lidl Hellas digital receipts (local CLI).

Primary path is the Firefox extension (ingest API); this module keeps the
local headless-Chromium sync for people running outside Docker without the
extension: --latest-only / --max / --headed. Fallback: --paste <file>."""
import argparse
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from db import init_db, get_db
from fetch_lib import (trim_receipt_text, receipt_body_date,
                       pick_filename, import_kept_receipt)

RECEIPT_DIR = os.path.join(BASE_DIR, "receipts")

HISTORY_URL = ("https://www.lidl-hellas.gr/mre/purchase-history"
               "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page={page}")
DETAIL_URL = ("https://www.lidl-hellas.gr/mre/purchase-detail"
              "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&t={tid}")
LOGIN_URL = "https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/"
PROFILE_DIR = os.path.join(BASE_DIR, "data", ".lidl-profile")


def _profile_dir():
    """Profile location; LIDL_PROFILE_DIR wins (Docker: mount it from /app/data)."""
    return os.environ.get("LIDL_PROFILE_DIR", PROFILE_DIR)

LAST_LOGIN_NOTE = None


def launch_context(pw, headed=False):
    """Persistent Chromium profile with automation fingerprints reduced.

    Lidl's SSO answers headless automation with a generic "system
    overload" refusal, so we drop the strongest detectable signals:
    the AutomationControlled blink flag, navigator.webdriver, and the
    missing window.chrome runtime object.

    NOTE: this profile directory holds a signed-in browser session —
    protect it like a password. Prefer the Firefox extension over storing
    LIDL_PASS.
    """
    profile = _profile_dir()
    os.makedirs(profile, mode=0o700, exist_ok=True)
    try:
        os.chmod(profile, 0o700)
    except OSError:
        pass
    ctx = pw.chromium.launch_persistent_context(
        _profile_dir(), headless=not headed,
        args=["--disable-blink-features=AutomationControlled",
              "--no-sandbox", "--disable-dev-shm-usage"])
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        "if (!window.chrome) { window.chrome = {runtime: {}}; }")
    return ctx


def is_login_redirect(url: str) -> bool:
    url = (url or "").lower()
    return "mre/" not in url and ("login" in url or "sign" in url or "auth" in url)


def get_lidl_creds():
    import settings_store as ss
    import secret_store as enc
    return ss.get("LIDL_USER"), enc.get_secret("LIDL_PASS")


def _click_main_next(page, timeout=5000) -> bool:
    """Click the wide blue ΕΠΟΜΕΝΟ form button.

    The header back link also reads ΕΠΟΜΕΝΟ — clicking it goes BACK a
    step (probed 2026-09-27: in-field Enter triggers it and the flow
    resets to the email stage). The submit button carries
    type="button" and sits below the fold of the header (y > 100).
    """
    for sel in ('button[type="button"]:has-text("ΕΠΟΜΕΝΟ")',
                'button[type="button"]:has-text("Next")'):
        try:
            page.click(sel, timeout=timeout)
            return True
        except Exception:
            continue
    try:
        btns = page.query_selector_all('button:has-text("ΕΠΟΜΕΝΟ")') or []
        for b in btns:
            try:
                box = b.bounding_box() or {}
            except Exception:
                continue
            if box.get("y", 0) > 100 and box.get("width", 0) > 200:
                b.click(timeout=timeout)
                return True
    except Exception:
        pass
    return False


def _classify_failure(body: str):
    """Map the login page text to a failure note (None = unknown)."""
    body = body or ""
    if "Υπερπληρότητα" in body or "system overload" in body.lower():
        return "overload"
    if "δημιουργία λογαριασμού" in body or "create an account" in body.lower():
        return "no-account"
    low = body.lower()
    if ("λάθος" in low or "λανθασμέν" in low or "incorrect" in low
            or "wrong password" in low or "invalid" in low):
        return "bad-password"
    return None


def _read_body(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def try_auto_login(page, user=None, pw=None) -> bool:
    global LAST_LOGIN_NOTE
    LAST_LOGIN_NOTE = None
    if user is None or pw is None:
        user, pw = get_lidl_creds()
    if not user or not pw:
        return False
    try:
        page.goto(LOGIN_URL)
        for sel in ('input[type="email"]', 'input[name="email"]', 'input[type="text"]'):
            try:
                page.fill(sel, user, timeout=8000)
                break
            except Exception:
                continue
        # Step 2 of Lidl's login: email first, then the main ΕΠΟΜΕΝΟ
        # button reveals the password field.
        if not _click_main_next(page):
            for sel in ('button:has-text("Next")', 'button[type="submit"]'):
                try:
                    page.click(sel, timeout=3000)
                    break
                except Exception:
                    continue
        try:
            page.wait_for_selector('input[type="password"]', state="visible", timeout=20000)
        except Exception:
            LAST_LOGIN_NOTE = _classify_failure(_read_body(page)) or "timeout"
            return False
        for sel in ('input[type="password"]', 'input[name="password"]'):
            try:
                page.fill(sel, pw, timeout=8000)
                break
            except Exception:
                continue
        # Submit via the blue button. Never via in-field Enter: that
        # activates the header back link and resets the flow to email.
        _click_main_next(page)
        for _ in range(5):
            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass
            if not is_login_redirect(page.url):
                return True
            note = _classify_failure(_read_body(page))
            if note:
                LAST_LOGIN_NOTE = note
                return False
        LAST_LOGIN_NOTE = _classify_failure(_read_body(page))
        return False
    except Exception:
        return False


def attempt_login(email: str, pw: str) -> bool:
    """Headless one-shot login check reusing the persistent profile."""
    from playwright.sync_api import sync_playwright
    email = (email or "").strip()
    if not email or not pw:
        return False
    with sync_playwright() as p:
        context = launch_context(p, headed=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return try_auto_login(page, email, pw)
        except Exception:
            return False
        finally:
            context.close()


def wait_for_login(page, timeout_s: int = 300) -> bool:
    """Poll until the user finishes logging in (or timeout/closed page)."""
    import time
    deadline = time.time() + timeout_s
    while True:
        try:
            url = page.url
        except Exception:
            return False
        if not is_login_redirect(url):
            return True
        if time.time() > deadline:
            return False
        time.sleep(2)


def collect_tids(page, max_pages: int = 0) -> list:
    tids: list = []
    seen: set = set()
    page_num = 1
    while True:
        if max_pages and page_num > max_pages:
            break
        if page_num > 1:
            page.goto(HISTORY_URL.format(page=page_num))
        try:
            # list renders via JS after goto; without this the query below
            # races page load and silently finds zero receipts
            page.wait_for_selector("a[href*='purchase-detail']", timeout=15000)
        except Exception:
            break
        found_new = False
        for el in page.query_selector_all("a[href*='purchase-detail']"):
            href = el.get_attribute("href") or ""
            m = re.search(r"[?&]t=([\w-]+)", href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                tids.append(m.group(1))
                found_new = True
        if not found_new:
            break
        if max_pages and page_num >= max_pages:
            break
        if not max_pages:
            # No explicit page cap: stop when a page yields no *new* ids
            # beyond what we already saw, unless a next-page link exists.
            nxt = page.query_selector("a[href*='page=']")
            if nxt is None:
                break
        page_num += 1
    return tids


def fetch_detail_text(page, tid: str) -> str:
    page.goto(DETAIL_URL.format(tid=tid))
    page.wait_for_selector("text=\u03a6\u039f\u03a1\u039f\u039b\u039f\u0393\u0399\u039a\u0397 \u0391\u03a0\u039f\u0394\u0395\u0399\u039e\u0397 \u0395\u039d\u0391\u03a1\u039e\u0397", timeout=15000)
    return page.inner_text("body")


def cmd_sync(latest_only: bool = False, max_pages: int = 0, headed: bool = False) -> int:
    from playwright.sync_api import sync_playwright
    init_db()
    new, skipped = 0, 0
    needs: list = []
    with sync_playwright() as p:
        context = launch_context(p, headed=headed)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(HISTORY_URL.format(page=1))
            if is_login_redirect(page.url):
                if not headed:
                    if try_auto_login(page):
                        page.goto(HISTORY_URL.format(page=1))
                        if is_login_redirect(page.url):
                            print("LOGIN NEEDED \u2014 auto-login failed, rerun with --headed and log in once")
                            return 3
                    else:
                        print("LOGIN NEEDED \u2014 rerun with --headed and log in once")
                        return 3
                if headed:
                    print("Waiting for login in the opened browser window (5 min max)...")
                    if not wait_for_login(page):
                        print("LOGIN NEEDED \u2014 login was not completed in time")
                        return 3
                    page.goto(HISTORY_URL.format(page=1))
                    if is_login_redirect(page.url):
                        print("LOGIN NEEDED \u2014 still on the login page, rerun with --headed")
                        return 3
            tids = collect_tids(page, max_pages)
            conn = get_db()
            try:
                seen_db = {r[0] for r in
                           conn.execute("SELECT t_id FROM fetch_state").fetchall()}
                for tid in tids:
                    if tid in seen_db:
                        skipped += 1
                        continue
                    try:
                        full = fetch_detail_text(page, tid)
                        kept = trim_receipt_text(full)
                        yy, mm, dd = receipt_body_date(kept)
                        name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
                        import_kept_receipt(conn, kept, name, tid)
                        os.makedirs(RECEIPT_DIR, exist_ok=True)
                        with open(os.path.join(RECEIPT_DIR, name), "w",
                                  encoding="utf-8") as f:
                            f.write(kept)
                        new += 1
                        seen_db.add(tid)
                        if latest_only:
                            break
                    except Exception:
                        needs.append(tid)
            finally:
                conn.close()
        finally:
            context.close()
    print(f"new: {new}, skipped: {skipped}, needs-review: {needs}")
    return 0


def cmd_paste(path: str, t_id) -> dict:
    init_db()
    with open(path, "r", encoding="utf-8") as f:
        full = f.read()
    kept = trim_receipt_text(full)
    yy, mm, dd = receipt_body_date(kept)
    conn = get_db()
    try:
        name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
        res = import_kept_receipt(conn, kept, name, t_id)
    finally:
        conn.close()
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    with open(os.path.join(RECEIPT_DIR, name), "w", encoding="utf-8") as f:
        f.write(kept)
    print(f"saved {name} ({res['items']} items, {res['total']:.2f} EUR)")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", help="path to full Ctrl+A page text file")
    ap.add_argument("--tid", default=None)
    ap.add_argument("--latest-only", action="store_true")
    ap.add_argument("--max", dest="max_pages", type=int, default=0,
                    help="max history pages to scan (0 = all)")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)
    if args.paste:
        cmd_paste(args.paste, args.tid)
        return 0
    if args.latest_only or args.max_pages or args.headed:
        return cmd_sync(latest_only=args.latest_only,
                        max_pages=args.max_pages, headed=args.headed)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
