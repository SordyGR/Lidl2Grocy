"""Build an AMO-ready upload zip from Lidl Parser/extension/.

Usage (repo root):
    python3 packaging/firefox/build.py            # writes packaging/firefox/dist/lidl2grocy-sync-<ver>.zip
    python3 packaging/firefox/build.py --check    # validate manifest + zip contents without writing

The zip contains the extension files at TOP level (manifest.json first),
which is what addons.mozilla.org expects for an "On your own" (unlisted,
self-distributed) submission. AMO returns a signed .xpi; that signed file
is what users install.
"""
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "Lidl Parser" / "extension"
OUT_DIR = Path(__file__).resolve().parent / "dist"

# Exact file list shipped to AMO — keep tight, no dev leftovers.
FILES = [
    "manifest.json",
    "background.js",
    "content_history.js",
    "content_detail.js",
    "popup.html",
    "popup.js",
    "icons/icon-48.png",
    "icons/icon-96.png",
    "icons/icon-128.png",
]


def load_manifest():
    try:
        return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"manifest.json unreadable/invalid JSON: {e}")


def check(manifest):
    errors = []
    for f in FILES:
        if not (EXT / f).is_file():
            errors.append(f"missing file: {f}")
    gid = (manifest.get("browser_specific_settings", {}).get("gecko", {}).get("id", ""))
    if "@" not in gid:
        errors.append("browser_specific_settings.gecko.id missing/not email-like "
                      "(required for On-your-own signing)")
    icons = manifest.get("icons", {})
    for s in ("48", "96", "128"):
        if icons.get(s) != f"icons/icon-{s}.png":
            errors.append(f"manifest icons[{s}] should be icons/icon-{s}.png")
    if manifest.get("manifest_version") != 2:
        errors.append("manifest_version must stay 2 for this codebase (browser_action + persistent background)")
    dcp = (manifest.get("browser_specific_settings", {}).get("gecko", {})
           .get("data_collection_permissions", {}).get("required", []))
    if not dcp:
        errors.append("browser_specific_settings.gecko.data_collection_permissions.required "
                      "missing (required for new AMO submissions since Nov 2025)")
    if errors:
        raise SystemExit("CHECK FAILED:\n- " + "\n- ".join(errors))
    print(f"check OK: version {manifest.get('version')}, id {gid}, {len(FILES)} files")


def build(manifest):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"lidl2grocy-sync-{manifest.get('version', 'unversioned')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(EXT / f, f)
    names = zipfile.ZipFile(out).namelist()
    assert names[0] == "manifest.json", names
    size = out.stat().st_size
    print(f"wrote {out} ({size} bytes, {len(names)} files)")
    return out


if __name__ == "__main__":
    m = load_manifest()
    check(m)
    if "--check" not in sys.argv:
        build(m)
