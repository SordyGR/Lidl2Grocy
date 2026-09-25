# Final Review — Lidl Receipt Auto-Fetch (2026-09-23)

Scope: final-state review, no git repo so no diff. Read spec, plan (Global Constraints + Review Focus),
ledger (Rulings + deferred minors), and all new/changed files listed in the review brief.
Read-only review: no files edited, nothing executed, no subagents.

## Verdict: CLEAN (no Critical / Important findings; Minor notes only, none blocking use)

## Triage (one line each)
- T3 unused `tmp_path` (`test_fetch_cli.py:4`): LEAVE — harmless unused pytest fixture arg, no runtime effect; optional lint tidy only.
- T4a unused `LOGIN_URL` (`fetch_lidl.py:19`): LEAVE — dead at runtime but serves as the spec-required top-of-file route constant / drift-fix documentation; zero side effects.
- T4b bare `except Exception` (`fetch_lidl.py:101-102`): LEAVE — loop continuity and needs-review accounting are preserved; recommend logging the exception message as a follow-up, non-blocking.
- Ledger Ruling self-cleanup in `test_fetch_cli.py`: LEAVE — sound; fully cleans the real DB/receipts on every normal pass/fail path (see residual edges below, all non-blocking).

## Interface match (fetch_lib.py <-> fetch_lidl.py <-> tests)
- `trim_receipt_text(full_text: str) -> str`, raises `ValueError` on missing markers
  (`fetch_lib.py:9-18`) — matches plan Task 1 signature; used by `cmd_paste`
  (`fetch_lidl.py:115`) and `cmd_sync` (`fetch_lidl.py:89`); tested
  (`test_fetch_lib.py:14-19,30-35`). Match.
- `receipt_body_date(kept) -> (yy, mm, dd)` (`fetch_lib.py:21-26`) — matches plan;
  callers unpack `yy, mm, dd` identically in both commands. Match.
- `filename_for_date(yy, mm, dd, seq)` (`fetch_lib.py:29-31`) — `seq=0` bare,
  `seq>=1` dotted; matches plan and Global Constraints. Match.
- `extract_total(kept) -> float` (`fetch_lib.py:34-38`) — first `ΣΥΝΟΛΟ <amount>`;
  used as the totals guard in `import_kept_receipt` (`fetch_lib.py:64`). Match.
- `pick_filename(receipt_dir, conn, yy, mm, dd) -> str` (`fetch_lib.py:45-54`) —
  checks disk AND `receipts.filename`, first non-colliding; matches plan. Match.
- `import_kept_receipt(conn, kept, filename, t_id) -> dict` (`fetch_lib.py:57-89`) —
  returns `{"filename","items","total","t_id"}`; raises `ValueError` on empty parse
  (`fetch_lib.py:62`) and total mismatch > 0.02 (`fetch_lib.py:64-65`); matches plan. Match.
- CLI `python fetch_lidl.py --paste <file> [--tid T]` prints
  `saved <filename> (<n> items, <total> EUR)` (`fetch_lidl.py:126`) — matches plan
  Task 3 contract and `test_fetch_cli.py:13` assertion (`"saved 26-09-22"`). Match.
- `is_login_redirect(url) -> bool` (`fetch_lidl.py:23-24`) — `mre/` absent +
  login/sign/auth present; matches plan Task 4 and all three
  `test_fetch_crawl.py:6-8` assertions. Match.
- Sync summary `new: {new}, skipped: {skipped}, needs-review: {needs}`
  (`fetch_lidl.py:107`) — matches plan Task 4 output contract. Match.
- No dead imports at runtime: `re` used by `collect_tids` (`fetch_lidl.py:39`);
  `argparse`/`sys` used by `main`. Trivial lint only: unused `sqlite3` import in
  `test_fetch_import.py:3` and unused `tmp_path` in `test_fetch_cli.py:4`.

## Spec coverage (design §2-§5 vs implementation)
- Trim markers (start Greek date line, end `ΕΠΙΣΤΡΟΦΕΣ…ΔΕΚΤΕΣ` inclusive, drop
  header/coupons/footer): COVERED (`fetch_lib.py:9-18`; test asserts coupons and
  `Lidl Hellas` header absent, `test_fetch_lib.py:14-19`).
- Filename scheme body date `22.09.26` → `26-09-22`, collisions `.1/.2/.3`, skip if
  `receipts.filename` exists: COVERED (`fetch_lib.py:21-31,45-54`; collision test
  `test_fetch_import.py:42-47`).
- Idempotency (`INSERT OR IGNORE`, items delete+reinsert, `fetch_state` pre-check):
  COVERED (`fetch_lib.py:68-88`, `fetch_lidl.py:81-86`; re-run test
  `test_fetch_import.py:28-40`).
- Totals guard (parsed vs `ΣΥΝΟΛΟ`, flag if diff > 0.02 €): COVERED as a raise
  (`fetch_lib.py:63-65`); sync path converts it to `needs-review`
  (`fetch_lidl.py:101-102`). `--paste` path lets it propagate (no silent wrong
  number) — correct, see Minor M2 on txt-kept wording.
- LOGIN NEEDED (stop, import nothing): COVERED (`fetch_lidl.py:75-77`, return 3
  before `get_db()`/any receipt write; `init_db()` table-ensure only). Match to
  Review Focus expectation (zero DB writes of receipt data).
- `--paste` fallback (no browser): COVERED (`fetch_lidl.py:111-127`, fixture
  `tests/data_paste.txt`, `test_fetch_cli.py`).
- Review Focus items: stacked Lidl Plus discounts accumulate (test asserts 0.16,
  `test_fetch_import.py:32-34`); kg line `0,304 kg x 3,79` exact floats (same KEPT
  fixture, total 3.77); same-date no-overwrite via `.1` suffix; expired login
  stops; unrecognizable detail page raises before any DB insert (txt not written
  on that path — see M2). All verified present.
- Credentials: COVERED — no password stored/logged/CLI-arg anywhere; only
  `PROFILE_DIR` session cookies (`fetch_lidl.py:20`), git-ignored
  (`.gitignore:1`).
- Destructive defaults: NONE FOUND — writes are confined to `receipts/`
  (`fetch_lidl.py:93-96,123-125`), `data/.lidl-profile/` (Playwright persistent
  context, `fetch_lidl.py:70-71`), and the DB via `init_db`/`get_db`. Deletes are
  scoped (`DELETE FROM items WHERE receipt_id=?`, `fetch_lib.py:74`; test cleanup
  deletes by exact filename + `t_id='CLITEST'`, `test_fetch_cli.py:28-34`).
- Rollout docs: COVERED (`requirements.txt:5-6` pins `playwright>=1.44`,
  `pytest>=8.0`; `README.md:28-38` Fetch section with install/chromium/
  `--latest-only`/`--max`/`--paste` + session note). `fetch_state` table present
  in SCHEMA (`db.py:62-66`).

## Minor findings (non-blocking, use is not gated on any of these)
- M1 — `fetch_lidl.py:101-102` bare `except Exception` swallows error detail.
  Why not blocking: the loop continues and the tid is still recorded in
  `needs-review`, so no silent loss and no crash-loop. Follow-up (optional):
  `except Exception as e: print(f"warn {tid}: {e}"); needs.append(tid)`.
- M2 — Spec §4 wording "trim/parse failures → txt kept, DB untouched": on the
  sync path a `trim_receipt_text` failure happens BEFORE the txt write
  (`fetch_lidl.py:89-96`), so the txt is not kept — the tid is listed in
  `needs-review` and the run is re-runnable, which preserves the operative
  guarantee (DB untouched, failure surfaced). Same ordered-write in `cmd_paste`
  (`fetch_lidl.py:119-125`). Not blocking; if the txt-kept wording is wanted
  literally, write the raw page text on failure as a follow-up.
- M3 — Spec §2 data-flow step 7 mentions `unmapped raws` in the summary; the
  printed summary covers `new / skipped / needs-review` (`fetch_lidl.py:107`)
  and the plan Task 4 contract only requires those three. Docs-level drift only.
- M4 — `test_fetch_cli.py` self-cleanup residual edges (all non-blocking):
  (a) a hard crash/timeout before stdout parsing leaves `name=""`, so the
  receipt/file rows would not be removed (the `CLITEST` fetch_state rows are
  still deleted); (b) if the real `26-09-22.txt` ever went missing the CLI could
  legitimately pick the bare name and the guard would refuse to clean it (by
  design — it must never touch the real file); (c) mid-run the test still
  touches the real DB/receipts (post-run clean, so parallel suite runs could
  observe it). Normal pass/fail paths clean fully, including the final
  `leftovers == []` assertion (`test_fetch_cli.py:38-43`).
- M5 — Style/lint only: `test_fetch_cli.py:4` unused `tmp_path`,
  `test_fetch_import.py:3` unused `sqlite3`, `fetch_lidl.py:19` unused
  `LOGIN_URL`. No functional impact.

## Files reviewed (final state)
`Lidl Parser/fetch_lib.py` (89 lines), `Lidl Parser/fetch_lidl.py` (150 lines),
`Lidl Parser/db.py` (SCHEMA, lines 1-70), `Lidl Parser/requirements.txt`,
`Lidl Parser/.gitignore`, `Lidl Parser/README.md`,
`Lidl Parser/tests/test_fetch_lib.py`, `Lidl Parser/tests/test_fetch_import.py`,
`Lidl Parser/tests/test_fetch_cli.py`, `Lidl Parser/tests/test_fetch_crawl.py`,
`Lidl Parser/tests/data_paste.txt` (fixture, skimmed).
