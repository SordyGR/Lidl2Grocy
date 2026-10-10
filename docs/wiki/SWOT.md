# SWOT analysis

## Strengths

- **Solves a real, local problem end-to-end**: Lidl Hellas → parsed lines →
  curated catalog → Grocy stock, with per-user splits no off-the-shelf tool
  offers for Greek households.
- **Review-gated automation**: two-key dedupe (tid + fiscal MAPK), manual-only
  Grocy push, conflict review on imports — the design earns trust with money
  data.
- **Resilient to outages**: Grocy-unreachable still renders from cache;
  extension failures resume; paste/upload/CLI fallbacks always exist.
- **242-test suite** with TDD discipline; Docker one-command deploy with
  volume-persisted data.
- **Privacy-conscious**: card last-4 only, secrets never rendered, local-first
  SQLite + self-hosted.

## Weaknesses

- **Single-retailer, single-country**: parser is Lidl-Hellas-specific; any
  site/format change breaks ingest until fixed.
- **Extension is sideload-only**: unload-on-close friction, no auto-update,
  Firefox-only (no Chrome port).
- **Bus factor one + Greek-language niche**: small audience, maintainer-bound.
- **Pre-1.0 schema churn**: `0.x` migrations accumulate; no stable API
  contract yet.
- **Two UI skins** to maintain (v1 default + v2 opt-in) until the cutover.

## Opportunities

- **Other Greek retailers via myDATA**: e-invoicing is mandatory in Greece —
  ΑΒ Βασιλόπουλος exposes receipts the same way, so a generic myDATA-source
  layer could multiply coverage.
- **Budget-app integrations**: Firefly III / Actual Budget receipt creation
  via API turns grocery lines into real budgets automatically.
- **Grocy completion**: finish ERP item creation + push flow → full
  pantry automation story.
- **Store listing**: AMO publication removes the biggest adoption hurdle.
- **Scheduled syncs + readable graphs**: set-and-forget freshness and
  shareable household reports widen the audience beyond tinkerers.

## Threats

- **Lidl anti-automation**: SSO throttling ("system overload"), page/markup
  changes, login walls — each can silently stop all ingest.
- **Grocy API drift**: upstream changes to products/stock/barcode endpoints
  break linking and pushing.
- **Firefox MV2 deprecation**: forces an MV3 port of the sweep engine.
- **Fiscal-format changes** (myDATA/MAPK layout) weaken the dedupe guarantee.
- **Scope creep** into a general finance app dilutes the grocery focus that
  makes the tool good.
