# Firefox upload — "On your own" (unlisted, self-distributed)

The extension source is `Lidl Parser/extension/` (Manifest V2). AMO signs it;
you distribute the **signed .xpi** yourself. No store listing, no Mozilla review
queue for listing — automated checks + (sometimes) a manual code review still apply.

## 0. What changed for this

- `manifest.json` gained `browser_specific_settings.gecko.id`
  (`lidl2grocy-sync@sordygr.github.io`) + `strict_min_version 142.0` (floor for
  `data_collection_permissions`: FF 140 desktop / 142 Android),
  `data_collection_permissions` (required: `browsingActivity`,
  `financialAndPaymentInfo` — the receipt text + page URLs synced to the user's
  own server; core function, no opt-out), `homepage_url`, and `icons/`
  (48/96/128) wired to `icons/` + `browser_action`.
- No `update_url` yet: users reinstall each release manually. To add auto-updates
  later, copy `updates.json.template` to HTTPS hosting and add
  `browser_specific_settings.gecko.update_url` before re-submitting.
- Version stays `1.1` — bump it for every new AMO submission (AMO rejects
  re-uploads of the same version).

## 1. Build the upload zip

```bash
python3 packaging/firefox/build.py
# → packaging/firefox/dist/lidl2grocy-sync-1.1.zip
```

The zip holds the extension files at **top level** (`manifest.json` first).
Never upload a zip containing a parent folder, and never upload the signed
`.xpi` back to AMO — always re-submit a fresh unsigned zip.

Optional lint (downloads `web-ext` on first run):

```bash
npx -y web-ext lint --source-dir "Lidl Parser/extension"
```

## 2. Submit to AMO → "On your own"

1. Sign in at https://addons.mozilla.org/developers/ (creates the add-on id
   binding on first submit; keep the id in `manifest.json` stable afterwards).
2. **Submit a New Add-on → On your own** (NOT "On this site").
3. Upload `packaging/firefox/dist/lidl2grocy-sync-1.1.zip`.
4. Fill in the version notes; in review notes explain the broad hosts:
   `http(s)://*/*` exists because the user self-hosts the Lidl2Grocy server at an
   arbitrary LAN/VPN URL entered in the popup, plus `*://*.lidl-hellas.gr/*` for
   the receipt crawl. All network traffic is user-configured server sync.
5. Finish submission → download the **signed .xpi** from AMO when ready.

## 3. Distribute / install

- Publish the signed `.xpi` on your own site/releases page over HTTPS.
- Users: Firefox → open the `.xpi` (or `about:addons` → gear → Install Add-on
  From File) → Accept → the add-on now persists across restarts and is signed.
- Keep the unsigned zip + the signed xpi per version; tag releases `vX.Y.Z`.

## 4. Next release

1. Bump `version` in `Lidl Parser/extension/manifest.json`.
2. Re-run `build.py`, submit the new zip as a new version of the same add-on
   (same gecko id), distribute the new signed xpi.

## 5. If AMO flags something

- Broad host permissions → expected warning; the review-notes text in §2 covers it.
- `persistent: true` background → harmless on Firefox (non-persistent background
  not required); leave as-is for compat.
- Missing `update_url` → informational only while you distribute manually.
