"""Extension 'send this receipt' contract: popup button, grab listener, tid parsing."""
import json
import pathlib
import subprocess

EXT = pathlib.Path(__file__).resolve().parent.parent / "extension"


def _read(name):
    return (EXT / name).read_text(encoding="utf-8")


def test_popup_has_send_this_receipt_button():
    html = _read("popup.html")
    assert "sendActive" in _read("popup.js")
    assert "Send this receipt" in html


def test_detail_script_answers_grab():
    assert '"grab"' in _read("content_detail.js") or "'grab'" in _read("content_detail.js")


def test_tid_from_url_parses_detail_links():
    src = _read("background.js")
    assert "tidFromUrl" in src
    probe = ("globalThis.browser = {runtime: {onMessage: {addListener() {}},"
             " sendMessage() { return Promise.resolve(); }}, tabs: {},"
             " storage: {local: {get: async () => ({})}}};\n"
             + src + """
console.log(JSON.stringify([
  tidFromUrl("https://www.lidl-hellas.gr/mre/purchase-detail?client_id=X&t=abc-123"),
  tidFromUrl("https://www.lidl-hellas.gr/mre/purchase-detail?t=abc-123&foo=1"),
  tidFromUrl("https://www.lidl-hellas.gr/mre/purchase-history?page=2"),
  tidFromUrl("")
]));
""")
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == ["abc-123", "abc-123", None, None]
