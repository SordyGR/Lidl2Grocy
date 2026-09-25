# SDD ledger — plan: docs/superpowers/plans/2026-09-24-grocy-link-plan.md
Pre-flight: Task1 produces get_config/fetch_catalog dict keys; Task2 consumes same keys; Task4 consumes suggest/find_orphans; Task5 consumes route context vars. No conflicts found.
Ruling: no git repo in workspace — skip git commit steps, implement files directly; progress tracked here. Cost if wrong: no per-task commits, history only in files.
Task 1: complete (tests: pytest tests/test_grocy_match.py -> 3/3 pass).
Task 2: complete (tests: pytest tests/test_grocy_match.py -> 7/7 pass).
Task 3: complete (tests: test_products_has_grocy_columns PASS).
Task 4: complete (tests: test_grocy_status_unconfigured PASS).
Task 5: complete (tests: pytest tests/test_grocy_match.py -> 11/11 pass; full suite 21/21 pass).
Ruling: Task5 link test passed before template work because link route shipped in Task4; added test_mappings_shows_grocy_ui as true Task5 RED. Cost if wrong: none, both behaviors now pinned.
Ruling: db.py SCHEMA index broke existing DBs (executescript creates index before ALTERs); removed index from SCHEMA, keep creation in init_db after column backfill. Cost if wrong: migration crash on user DBs.
Final: fixed accent folding (unicodedata NFD in _norm) — test_accent_folding_suggests GREEN, suite 26/26.
Final: fixed barcode dash/space normalization (_norm_barcode) — test_barcode_dash_normalized RED->GREEN, suite 26/26.
Final: fixed grocy_status hint + stale flag — test_grocy_status_hint RED->GREEN, suite 26/26.
Final: fixed orphan flag in mappings (orphans set + stale link badge) — test_mappings_flags_orphan_link RED->GREEN, suite 26/26.
Final: fixed stale-cache fallback <30min with stale:true — test_stale_cache_fallback RED->GREEN, suite 26/26.
Final: minor (deferred): free-text grocy_name can mismatch grocy_id label.
Final: minor (deferred): request.form[raw] KeyError on crafted POST.
Final: minor (deferred): link to nonexistent local raw flashes success (no rowcount check).
Final: minor (deferred): duplicate Grocy names resolve to first id.
Final: minor (deferred): tests are cwd-dependent (run from Lidl Parser/).
Ruling: keep .superpowers workspace (no git history to preserve record). Cost if wrong: leftover ignored dir.
