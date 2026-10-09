# Task 5 brief — README + full-sync verification (+ test self-cleanup fix)
Source: `docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md`, Task 5 (verbatim, plus binding controller rulings at the end).

**Files:**
- Modify: `Lidl Parser/README.md` (append Fetch section)
- Modify: `Lidl Parser/tests/test_fetch_cli.py` (add self-cleanup — see ruling 1; assertion unchanged)
- Test: verification commands only otherwise

**Interfaces:**
- Consumes: Tasks 1–4 complete.

- [ ] **Step 1: Append README Fetch section**

```markdown
## Fetch (auto-download from Lidl Hellas)

pip install -r requirements.txt
playwright install chromium
python fetch_lidl.py --latest-only   # daily use
python fetch_lidl.py --max 5         # cap history pages
python fetch_lidl.py --paste pasted.txt --tid 14000...  # manual fallback, no login
```

First run opens a browser: log in once at lidl-hellas.gr, then rerun. Session persists in `data/.lidl-profile/` (git-ignored).

- [ ] **Step 2: Run full test suite**

Run: `cd "Lidl Parser" && python -m pytest tests/ -v`
Expected: PASS, all tests green

- [ ] **Step 3: Run full sync dry check**

Run: `cd "Lidl Parser" && python fetch_lidl.py --max 2`
Expected: only genuinely new receipts imported; second run imports 0

- [ ] **Step 4: Commit** — SKIPPED by controller ruling (no git repo). Leave files on disk.

## Controller rulings (binding, override the plan where they conflict)
1. FIX the pollution defect: `tests/test_fetch_cli.py` currently writes a real receipt + DB rows on EVERY run and relies on manual cleanup — this already caused a data-loss incident during Task 4 (controller deleted a manually saved receipt with an over-broad LIKE cleanup, then restored it; DB verified back to baseline). Wrap the subprocess call in try/finally: after asserting, delete the created receipt file named in stdout (parse it from the `saved <name>` output — NEVER a hardcoded or glob name, and refuse to delete unless the name matches the expected dated pattern with a numeric suffix), delete its items + receipt rows and `fetch_state` rows with `t_id='CLITEST'` via sqlite3, and assert `fetch_state` has no CLITEST row. Keep the `saved 26-09-22` assertion byte-identical. Use `sqlite3` from stdlib (already imported as needed).
2. Step 3 (`--max 2`) will hit the login wall (no credentials): EXPECTED result is `LOGIN NEEDED`, exit 3, zero writes — verify + report before/after counts (receipts, files). Do NOT attempt any login.
3. After ALL test runs, verify + report final state: baseline receipt/item counts unchanged, `fetch_state` empty, `receipts/` contains exactly one file matching the manual receipt name. Any deviation = clean it and say so, or report BLOCKED.
