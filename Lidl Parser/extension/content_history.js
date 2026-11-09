// Runs on purchase-history pages: report tids + pager state, or logged-out.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "history", loggedOut: true, url: location.href});
    return;
  }
  // The receipt list renders asynchronously after page load, and much slower
  // on a cold start (SSO redirect + bundle + API calls) — poll for the full
  // tab budget before concluding the page is empty, or a slow render looks
  // like "no links". Must stay under loadInTab's 30 s timeout in background.js.
  const POLL_BUDGET_MS = 28000;
  const t0 = Date.now();
  const timer = setInterval(() => {
    const tids = [];
    const seen = new Set();
    for (const a of document.querySelectorAll("a[href*='purchase-detail']")) {
      const m = /[?&]t=([\w-]+)/.exec(a.getAttribute("href") || "");
      if (m && !seen.has(m[1])) { seen.add(m[1]); tids.push(m[1]); }
    }
    const v = location.href.toLowerCase();
    if (!v.includes("mre/") && /login|sign|auth/.test(v)) {
      clearInterval(timer);
      browser.runtime.sendMessage({type: "history", loggedOut: true, url: location.href});
    } else if (tids.length || Date.now() - t0 > POLL_BUDGET_MS) {
      clearInterval(timer);
      const hasNext = !!document.querySelector("a[href*='page=']");
      // textLen lets the background tell "page rendered but genuinely
      // empty" (full site chrome = kilobytes of text) apart from "page never
      // rendered" (spinner/blank/SSO interstitial = near-empty body).
      const textLen = document.body ? document.body.innerText.length : 0;
      browser.runtime.sendMessage({type: "history", loggedOut: false, tids, hasNext, textLen,
                                   url: location.href, title: document.title});
    }
  }, 500);
})();
