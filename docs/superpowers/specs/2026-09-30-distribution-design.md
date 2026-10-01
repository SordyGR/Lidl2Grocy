# Distribution design: prebuilt GHCR image + portable Windows zip

Date: 2026-09-30. Status: approved (with linux/arm64). Version anchor: 0.9.0.

## Goal

Two download-and-run paths, no clone/build/python knowledge required:

1. **Prebuilt image** — `docker compose up -d` against `ghcr.io/sordygr/lidl2grocy`.
2. **Portable Windows zip** — download, unzip, double-click `Start.bat`,
   browser opens on the app.

Non-goals: Docker Hub, installer wizards, macOS/Linux desktop packs,
auto-updating clients, Chrome port, store-listed extension.

## A. Container publishing

### Components

- `.github/workflows/docker-publish.yml` (new): triggers on tags `v*`
  (and manual dispatch). Steps: checkout, QEMU setup, Buildx, login to GHCR
  with `GITHUB_TOKEN`, build `Lidl Parser/` context, push tags
  `ghcr.io/sordygr/lidl2grocy:<semver>` + `:latest`, platforms
  `linux/amd64,linux/arm64`.
- `compose.yml` (changed): `image: ghcr.io/sordygr/lidl2grocy:${TAG:-latest}`
  replaces `build: .`; ports, env_file, volumes, restart unchanged.
- `compose.dev.yml` (new): single-service override re-adding `build: .`
  for local development.
- Repo setting (manual, one time): Actions → General → Workflow permissions
  → *Read and write* (for `packages:write`).

### Data flow

Tag `vX.Y.Z` → workflow builds/pushes → downloader copies `compose.yml` +
`.env.example`→`.env` → `docker compose up -d` → migrate runs in CMD as
today → `:5090`. Developer flow: add `-f compose.dev.yml --build`.

### Error handling

- Push failure (permissions): workflow fails loudly before any `latest` move;
  tags are immutable per release, `:latest` only moves on full success.
- Downloader on arm64 (Pi): same tag, no extra steps.
- Existing server: unchanged behavior after adding `-f compose.dev.yml`.

## B. Portable Windows zip

### Components (all new under `packaging/windows/`)

- `Start.bat`: finds bundled `python/python.exe`, runs `launcher.py`.
- `launcher.py`: (1) data dir = folder next to the bat (`data/`,
  `receipts/` created if missing); (2) `SECRET_KEY`: reuse
  `data/.secret` if present else `secrets.token_urlsafe(32)` written with
  owner-only ACL note, exported as env for the child; (3) run `migrate.py`;
  (4) serve `app:app` via **waitress** on `127.0.0.1:5000` (gunicorn is
  Unix-only); (5) `webbrowser.open()` the URL.
- `requirements-standalone.txt`: Flask, pandas, rapidfuzz, openpyxl,
  requests, cryptography, waitress. Pinned `==` at build time by CI
  (`pip freeze` snapshot stored next to the zip as `freeze.txt`).
- `README.txt`: 5-line usage (unzip anywhere → double-click → browser),
  data-location note, Firefox sideload pointer for Lidl auto-sync,
  paste/upload works with nothing else.
- CI job (same workflow, Windows runner, on tags): download CPython
  embeddable zip, `pip install -r requirements-standalone.txt --target`,
  assemble `Lidl2Grocy-<VERSION>-win64.zip` (app + python + launcher +
  README + freeze), attach to the GitHub release. Version string read from
  root `VERSION`, never hardcoded.

### Decisions

- B1 preinstalled-wheels zip (~150 MB, offline) over slim-installer zip:
  the audience trades download size for zero decisions.
- Minimal `app.py` env hooks (added during implementation — the "no app.py
  changes" premise was wrong: `SECRET_KEY` was hardcoded and `RECEIPT_DIR`
  had no hook): `SECRET_KEY`, `RECEIPT_DIR` read from env with fallbacks
  preserving Docker/dev behavior; `LIDL_DB` hook already existed and the
  launcher sets all three to the zip root.
- Grocy optional as today; extension sideload documented, not bundled.

### Error handling

- Missing/corrupt bundle: `Start.bat` prints the failing step and pauses
  (`pause`), nothing is half-started (migrate runs before serve).
- Port 5000 taken: launcher tries 5000–5009, opens whichever bound.
- First-run reinstall of wheels never happens (preinstalled); a
  `--repair` flag re-runs migrate only.

## Testing

- Existing 185-test suite stays green (no app behavior change).
- New tests: launcher unit tests (secret reuse/generation, dir creation,
  port-pick logic factored into pure functions); YAML validity test for
  compose files + workflow (parse + assert image/pull tags); manifest
  version-vs-VERSION consistency check.
- Release smoke (manual checklist in workflow annotations): boot image →
  `/api/ext/status` returns 401; boot zip → browser page loads, paste import
  works.

## Rollout order

1. Compose split + README downloader docs (works before first publish).
2. Publish workflow (test with a `v0.9.0` tag).
3. Windows packaging + release attachment.
