"""Fetch Lidl Hellas digital receipts. Fallback: --paste <file>."""
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


def is_login_redirect(url: str) -> bool:
    return "mre/" not in url and ("login" in url or "sign" in url or "auth" in url)


def get_lidl_creds():
    import settings_store as ss
    return ss.get("LIDL_USER"), ss.get("LIDL_PASS")


def try_auto_login(page) -> bool:
    user, pw = get_lidl_creds()
    if not user or not pw:
        return False
    try:
        page.goto(LOGIN_URL)
        for sel in ('input[type="email"]', 'input[name="email"]', 'input[type="text"]'):
            try:
                page.fill(sel, user, timeout=5000)
                break
            except Exception:
                continue
        for sel in ('input[type="password"]', 'input[name="password"]'):
            try:
                page.fill(sel, pw, timeout=5000)
                break
            except Exception:
                continue
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)
        return not is_login_redirect(page.url)
    except Exception:
        return False


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
            m = re.search(r"[?&]t=(\d+)", href)
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
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=not headed)
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
