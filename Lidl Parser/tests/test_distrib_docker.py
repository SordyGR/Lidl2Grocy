def test_compose_pull_first_and_dev_override():
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    yml = (base / "compose.yml").read_text(encoding="utf-8")
    assert "image: ghcr.io/sordygr/lidl2grocy:${TAG:-latest}" in yml
    assert "build:" not in yml
    dev = (base / "compose.dev.yml").read_text(encoding="utf-8")
    assert "build: ." in dev
    assert "lidl-parser" in dev


def test_publish_workflow_content():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent.parent
          / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    assert "ghcr.io/sordygr/lidl2grocy" in wf
    assert "linux/amd64,linux/arm64" in wf
    assert "packages: write" in wf
    assert "cat VERSION" in wf  # tag-vs-VERSION drift guard
    assert "Lidl Parser" in wf  # build context


def test_release_job_content():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent.parent
          / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    assert "windows-latest" in wf
    assert "requirements-standalone.txt" in wf
    assert "freeze.txt" in wf
    assert "Lidl2Grocy-" in wf and "win64.zip" in wf
    assert "contents: write" in wf  # release job must create GitHub releases
    # Personal seed CSVs are gitignored and absent from CI checkouts (migrate
    # skips them); the zip must not reference them or Assemble fails.
    assert "product_map.csv" not in wf
