# Prebuilt GHCR image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone can run the app with `docker compose up -d` from a published image, no clone/build needed.

**Architecture:** GitHub Actions builds `Lidl Parser/` on `v*` tags and pushes
linux/amd64+arm64 to GHCR; `compose.yml` goes pull-first, `compose.dev.yml`
keeps the local-build override for developers.

**Tech Stack:** GitHub Actions, docker/buildx (QEMU for arm64), GHCR, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-30-distribution-design.md` (section A)

## Global Constraints

- Image name MUST be lowercase: `ghcr.io/sordygr/lidl2grocy` (repo owner
  `SordyGR` has capitals; Docker rejects uppercase).
- Compose files live in `Lidl Parser/`; service name stays `lidl-parser`;
  ports `5090:5090`, `env_file: [.env]`, volumes `./data:/app/data` and
  `./receipts:/app/receipts`, `restart: unless-stopped` all unchanged.
- Tags pushed: `vX.Y.Z` (from git ref) + `latest` (only on full success).
- No app.py/db.py behavior changes in this plan.
- Git commit/tag/push is done by the human in their own terminal, never by
  the agent. Task steps end at verification.
- Shell is Windows PowerShell: no `tail`, no `&&` chaining.

## Review Focus

- Tag/VERSION drift (release cut from a tag that disagrees with `VERSION`) →
  workflow fails closed when `v$(cat VERSION)` != git tag; pinned by the
  workflow-content test in Task 2.
- GHCR permission denied on first publish → one-time repo setting
  (Actions → General → Workflow permissions → Read and write); documented in
  Task 3, surfaced by workflow failure otherwise.
- Existing server owner flow breaks (compose no longer builds) → covered by
  `compose.dev.yml` + docs; pinned by dev-override test in Task 1.
- Downloader without `.env` gets a boot crash → README tells them to copy
  `.env.example` first; compose keeps `env_file` (missing file = clear error).
- arm64 QEMU slowness/timeout → `platforms` line is one place; documented
  fallback to drop arm64 is NOT done silently (human decision).

---

### Task 1: Compose split (pull-first + dev override)

**Files:**
- Modify: `Lidl Parser/compose.yml`
- Create: `Lidl Parser/compose.dev.yml`
- Test: `Lidl Parser/tests/test_distrib_docker.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `compose.yml` with `image: ghcr.io/sordygr/lidl2grocy:${TAG:-latest}`;
  `compose.dev.yml` adding `build: .` to service `lidl-parser`.

- [ ] **Step 1: Write the failing test**

```python
def test_compose_pull_first_and_dev_override():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    yml = (base / "compose.yml").read_text(encoding="utf-8")
    assert "image: ghcr.io/sordygr/lidl2grocy:${TAG:-latest}" in yml
    assert "build:" not in yml
    dev = (base / "compose.dev.yml").read_text(encoding="utf-8")
    assert "build: ." in dev
    assert "lidl-parser" in dev
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_distrib_docker.py::test_compose_pull_first_and_dev_override -v`
(CWD: `Lidl Parser/`)
Expected: FAIL (FileNotFoundError on compose.dev.yml / assertion on image:)

- [ ] **Step 3: Write minimal implementation**

`Lidl Parser/compose.yml`:
```yaml
services:
  lidl-parser:
    image: ghcr.io/sordygr/lidl2grocy:${TAG:-latest}
    ports: ["5090:5090"]
    env_file: [.env]
    volumes: ["./data:/app/data", "./receipts:/app/receipts"]
    restart: unless-stopped
```

`Lidl Parser/compose.dev.yml`:
```yaml
services:
  lidl-parser:
    build: .
    image: ghcr.io/sordygr/lidl2grocy:dev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_distrib_docker.py -v`
Expected: PASS

### Task 2: Publish workflow

**Files:**
- Create: `.github/workflows/docker-publish.yml`
- Test: `Lidl Parser/tests/test_distrib_docker.py` (append)

**Interfaces:**
- Consumes: `VERSION` file content for the drift-guard comparison.
- Produces: workflow that tags `vX.Y.Z` + `latest` on tag push.

- [ ] **Step 1: Write the failing test** (append to same file)

```python
def test_publish_workflow_content():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent.parent
          / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    assert "ghcr.io/sordygr/lidl2grocy" in wf
    assert "linux/amd64,linux/arm64" in wf
    assert "packages: write" in wf
    assert "cat VERSION" in wf  # tag-vs-VERSION drift guard
    assert "./Lidl Parser" in wf or "Lidl Parser" in wf  # build context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_distrib_docker.py::test_publish_workflow_content -v`
Expected: FAIL (FileNotFoundError)

- [ ] **Step 3: Write minimal implementation**

`.github/workflows/docker-publish.yml`:
```yaml
name: publish
on:
  push:
    tags: ["v*"]
  workflow_dispatch: {}
permissions:
  contents: read
  packages: write
jobs:
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Tag matches VERSION
        run: test "v$(cat VERSION)" = "${GITHUB_REF_NAME}"
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: ./Lidl Parser
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/sordygr/lidl2grocy:${{ github.ref_name }}
            ghcr.io/sordygr/lidl2grocy:latest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_distrib_docker.py -v`
Expected: PASS

### Task 3: Downloader + dev docs in README

**Files:**
- Modify: `README.md` (Setup Docker section)
- Test: none new (Task 1–2 tests cover files; docs verified by review)

- [ ] **Step 1: Edit README Setup (Docker) section** to this content
  (replacing the clone/build-first instructions, keeping `.env` table):

```markdown
## Setup (Docker — prebuilt image, no build needed)

Prereqs: Docker Engine + Compose plugin, nothing else.

```bash
mkdir lidl2grocy && cd lidl2grocy
curl -O https://raw.githubusercontent.com/SordyGR/Lidl2Grocy/main/Lidl%20Parser/compose.yml
curl -O https://raw.githubusercontent.com/SordyGR/Lidl2Grocy/main/Lidl%20Parser/.env.example
cp .env.example .env   # then fill in values (see below)
docker compose up -d
```

Open `http://<server-ip>:5090`. Pin a version with `TAG=v0.9.0 docker compose
up -d` (defaults to `latest`).

Developers (local build): check out the repo and run
`docker compose -f compose.yml -f compose.dev.yml up -d --build`
from `Lidl Parser/`. First publish needs repo Settings → Actions →
General → Workflow permissions → **Read and write**.
```

- [ ] **Step 2: Run the full suite**

Run: `pytest -q` (CWD: `Lidl Parser/`)
Expected: all green (185 + new tests).
