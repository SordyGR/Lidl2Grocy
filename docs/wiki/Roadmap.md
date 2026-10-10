# Roadmap

## Future features

1. **Firefly III / Actual Budget integration** — create transactions/receipts
   automatically through their APIs from reviewed receipt lines (mirror the
   Grocy rule: review-gated, never silent).
2. **Scheduled syncs** — server-side timer for extension-independent refresh
   windows plus notifying on new receipts (respects the never-hammer-Lidl
   rule: sequential, throttled, backoff on refusal).
3. **Better graphs** — readable defaults (labels, contrast, mobile), exportable
   chart images for household sharing.
4. **More Greek retailers via myDATA** — e-invoicing is mandatory in Greece
   (ΑΒ Βασιλόπουλος already exposes digital receipts, others likely too):
   abstract a fiscal-source layer (Lidl scraper first, myDATA-format parsers
   next) behind the same trim → parse → dedupe pipeline.
5. **Complete Grocy item creation & pushing** — finish the ERP new-item flow
   (purchase/stock units, conversion factors, barcodes, groups) and harden
   push (idempotent re-push, per-line status ledger).

## Versioning scheme

SemVer `MAJOR.MINOR.PATCH`; root `VERSION` is the source of truth; releases
are tags `vX.Y.Z` on `main`.

| Bump | When |
|------|------|
| MAJOR | Breaking change (DB rebuild, API/route removal) |
| MINOR | New feature (page, endpoint, extension capability) |
| PATCH | Bug fix |

Pre-1.0 (`0.x`, current `0.11.0`): schema and ingest API may evolve; each MINOR
notes migrations. Extension manifest version tracks app `MAJOR.MINOR`.

**1.0.0 criteria**: extension store-listed (or stable sideload story) +
frozen ingest API + green fresh-install + completed Grocy push flow.
