# Task 4 brief — Playwright crawler wired to the import path
Source: `docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md`, Task 4 (verbatim, plus controller rulings at the end).

**Files:**
- Modify: `Lidl Parser/fetch_lidl.py` (append crawl commands — keep `cmd_paste`/`main` behavior; extend `main()` with new flags)
- Modify: `Lidl Parser/requirements.txt` (append `playwright>=1.44` and `pytest>=8.0`)
- Create: `Lidl Parser/.gitignore` (add `data/.lidl-profile/`)
- Create: `Lidl Parser/tests/test_fetch_crawl.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3 (do NOT modify `fetch_lib.py`, `db.py`, existing tests, or the fixture).
- Produces: `python fetch_lidl.py [--latest-only] [--max N] [--headed]` syncing missing receipts then printing `new / skipped / needs-review`.
- Produces (pure helper, tested): `is_login_redirect(url: str) -> bool` — True when the browser landed on the login flow instead of `mre/`.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_crawl.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fetch_lidl import is_login_redirect

def test_login_redirect_detection():
    assert is_login_redirect("https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/")
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=1")
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-detail?client_id=GreeceRetailClient&t=14000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_crawl.py -v`
Expected: FAIL with `cannot import name 'is_login_redirect'`

- [ ] **Step 3: Install browser dep (one-time)**

Run: `pip install playwright && playwright install chromium`
Expected: install succeeds

- [ ] **Step 4: Append crawler to fetch_lidl.py**

```python
HISTORY_URL = ("https://www.lidl-hellas.gr/mre/purchase-history"
               "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page={page}")
DETAIL_URL = ("https://www.lidl-hellas.gr/mre/purchase-detail"
              "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&t={tid}")
LOGIN_URL = "https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/"
PROFILE_DIR = os.path.join(BASE_DIR, "data", ".lidl-profile")


def is_login_redirect(url: str) -> bool:
    return "mre/" not in url and ("login" in url or "sign" in url or "auth" in url)
```

Add functions `collect_tids(page, max_pages)`, `fetch_detail_text(page, tid)`, `cmd_sync(latest_only, max_pages, headed)`:
  - launch persistent context at PROFILE_DIR; goto HISTORY_URL page 1; if `is_login_redirect(page.url)`, print `LOGIN NEEDED — rerun with --headed and log in once` and return 3 (importing nothing);
  - collect `t=` ids via `a[href*='purchase-detail']` across pages;
  - skip tids already in `fetch_state`;
  - per tid: goto DETAIL_URL, wait for `text=ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ`, `page.inner_text("body")` → `trim_receipt_text` → `pick_filename` → write txt → `import_kept_receipt` → count new/skipped/needs-review;
  - `--latest-only` stops after the first successful new import.

- [ ] **Step 5: Run crawler unit test plus live verify latest-only**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_crawl.py -v`
Expected: PASS

Run: `cd "Lidl Parser" && python fetch_lidl.py --latest-only`
Expected: prints `new: 0|1, skipped: N, needs-review: [...]`; newest receipt present in app dashboard

- [ ] **Step 6: Verify idempotency**

Run: `cd "Lidl Parser" && python fetch_lidl.py --latest-only` again
Expected: prints `new: 0`

- [ ] **Step 7: Commit** — SKIPPED by controller ruling (no git repo). Leave files on disk.

## Controller rulings (binding)
1. You have NO Lidl credentials and must NOT attempt any login. The live site run in Step 5 will therefore hit the login wall: the EXPECTED and CORRECT result is `LOGIN NEEDED — rerun with --headed and log in once`, exit code 3, and ZERO changes to `data/lidl.db` and `receipts/`. Verify and report exactly that (before/after receipt count + stdout). The real logged-in sync will be done later by the user. Cost if wrong: none as long as nothing is written.
2. Keep `from fetch_lidl import ...` import-safe: the Playwright import must live INSIDE `cmd_sync` (function-level import), so the unit test and `--paste` path work without Playwright installed.
3. Add `--latest-only` (store_true), `--max N` (int, default 0 = all pages), `--headed` (store_true) to the existing argparse in `main()` without changing `--paste`/`--tid` behavior.
4. `cmd_sync` must call `init_db()` at start (ensures `fetch_state` exists) and print exactly one summary line `new: <n>, skipped: <n>, needs-review: [...]`.
