# Lidl Receipt Auto-Fetch — Design Spec (2026-09-23)

## 1. Intent
Automate the manual chain: log in at lidl-hellas.gr → open
`mre/purchase-history` → click latest `purchase-detail?t=...` →
Ctrl+A/Copy kept receipt block → save as `receipts/YY-MM-DD[.1/.2].txt` → upload.
Approved approach: **A (Playwright + saved session)**, with **B (direct API)** as documented fallback.

## 2. Architecture & components
New `Lidl Parser/fetch_lidl.py` reusing existing code; no parser/schema changes except one new table.
- **Browser (Playwright Chromium):** persistent profile `data/.lidl-profile/` (git-ignored).
  Headed on first login, headless afterwards.
- **Crawler:** paginates `purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=N`,
  collects `purchase-detail?...&t=<id>` links.
- **Extractor:** per unseen `t=`, loads detail page, reads full text, trims to kept block.
- **Importer:** reuses `parse_receipt`, `parse_receipt_date`, `smart_match`, `build_memory`;
  inserts into `receipts`/`items` exactly like `/upload`.
- **State:** new table `fetch_state(t_id TEXT UNIQUE, filename TEXT, fetched_at TEXT)`.
- **Fallback B:** same trim→name→import functions; crawler replaceable by `--paste` mode.

## 3. Data flow
1. Launch Chromium with profile; if session invalid → headed login, user logs in once (email+password, no 2FA), session saved.
2. Walk history pages until empty/no new `t=` links. Flags: default full missing-sync;
   `--latest-only`, `--max N`.
3. For each `t_id` not in `fetch_state`: open detail, wait for receipt text, extract.
4. Trim: start = Greek date line (e.g. `22 Σεπ 2026`); end = `ΕΠΙΣΤΡΟΦΕΣ...ΔΕΚΤΕΣ` inclusive.
   Discard site header/footer, coupons-redeemed, store block, newsletter.
5. Filename from receipt body date `22.09.26` → `26-09-22`, collisions → `.1/.2/.3`;
   skip if `receipts.filename` exists.
6. Parse/match/insert (handles `έκπτωση Lidl Plus`, stacked discounts, `0,304 kg x 3,79 EUR/kg`);
   commit per receipt; record `fetch_state`.
7. Summary: `new / skipped / needs-review / unmapped raws`.

## 4. Error handling & idempotency
- Re-runs safe: `INSERT OR IGNORE` on filename, items delete+reinsert, `fetch_state` pre-check.
- Login expired → stop with `LOGIN NEEDED` message; no password handling.
- Trim/parse failures → txt kept, DB untouched, listed as `needs-review`.
- Totals check: parsed total vs `ΣΥΝΟΛΟ` line, flag if diff > 0.02 €.
- Markers/routes as top-of-file constants for easy drift fixes.

## 5. Testing, credentials & rollout
- Credentials: never stored/logged; only session cookies in ignored profile dir.
- Tests: (1) pasted 22-Sep text → byte-identical kept block, correct filename, total 82.72 €;
  (2) live `--latest-only` vs manual txt; (3) second full-sync imports 0.
- Rollout: `pip install playwright && playwright install chromium`; README Fetch section;
  `migrate.py` and `/upload` untouched.
