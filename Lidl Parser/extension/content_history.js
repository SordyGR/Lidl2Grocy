// Runs on purchase-history pages: report tids + pager state, or logged-out.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "history", loggedOut: true, url: location.href});
    return;
  }
  // The receipt list renders asynchronously after page load (the old
  // server scraper waited up to 15 s for it) — poll before concluding
  // the page is empty, or a slow render looks like "no links".
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
    } else if (tids.length || Date.now() - t0 > 15000) {
      clearInterval(timer);
      const hasNext = !!document.querySelector("a[href*='page=']");
      browser.runtime.sendMessage({type: "history", loggedOut: false, tids, hasNext,
                                   url: location.href, title: document.title});
    }
  }, 500);
})();
