"""Popup settings persistence: every keystroke/toggle lands in storage.local,
and a fresh open restores everything (server, token, page cap, full-scan)."""
import json
import pathlib
import subprocess

EXT = pathlib.Path(__file__).resolve().parent.parent / "extension"

_HARNESS = """
function makeEls() {
  const els = {};
  for (const id of ["serverUrl", "token", "pageCap", "fullScan",
                    "sync", "cancel", "sendActive", "status"]) {
    els[id] = {value: "", checked: false, disabled: false, textContent: "",
               _h: {},
               addEventListener(t, f) { (this._h[t] = this._h[t] || []).push(f); },
               async fire(t) { for (const f of (this._h[t] || [])) await f(); }};
  }
  return els;
}
function makeBrowser(store, tabUrl) {
  return {
    storage: {local: {
      get: async (defs) => ({...defs, ...store}),
      set: async (vals) => { Object.assign(store, vals); },
    }},
    tabs: {query: async () => [{url: tabUrl || "", id: 7}],
           sendMessage: async () => {}},
    runtime: {onMessage: {addListener() {}},
              sendMessage: async () => {}},
  };
}
"""


def _run(probe_body, seed=None, tab_url=""):
    src = (EXT / "popup.js").read_text(encoding="utf-8")
    probe = (_HARNESS + f"""
globalThis.__store = {json.dumps(seed or {})};
globalThis.browser = makeBrowser(globalThis.__store, {json.dumps(tab_url)});
globalThis.__els = makeEls();
globalThis.document = {{getElementById: (id) => globalThis.__els[id]}};
(async () => {{
""" + src + probe_body + """
})();
""")
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_typing_persists_without_sync_click():
    res = _run("""
      await new Promise((r) => setTimeout(r, 20));
      globalThis.__els.serverUrl.value = "http://192.168.2.10:5090/";
      globalThis.__els.token.value = "tok123";
      globalThis.__els.pageCap.value = "9";
      await globalThis.__els.serverUrl.fire("input");
      await globalThis.__els.token.fire("input");
      await globalThis.__els.pageCap.fire("input");
      await new Promise((r) => setTimeout(r, 20));
      console.log(JSON.stringify(globalThis.__store));
    """)
    assert res["serverUrl"] == "http://192.168.2.10:5090"
    assert res["token"] == "tok123"
    assert res["pageCap"] == 9


def test_fullscan_toggle_persists_and_restores():
    res = _run("""
      await new Promise((r) => setTimeout(r, 20));
      globalThis.__els.fullScan.checked = true;
      await globalThis.__els.fullScan.fire("change");
      await new Promise((r) => setTimeout(r, 20));
      console.log(JSON.stringify(globalThis.__store));
    """)
    assert res["fullScan"] is True
    res = _run(
        """
      await new Promise((r) => setTimeout(r, 20));
      console.log(JSON.stringify({
        url: globalThis.__els.serverUrl.value,
        token: globalThis.__els.token.value,
        cap: String(globalThis.__els.pageCap.value),
        full: globalThis.__els.fullScan.checked,
      }));
      """,
        seed={"serverUrl": "http://s:5090", "token": "abc",
              "pageCap": 7, "fullScan": True},
    )
    assert res == {"url": "http://s:5090", "token": "abc",
                   "cap": "7", "full": True}
