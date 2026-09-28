// Runs on purchase-history pages: report tids + pager state, or logged-out.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "history", loggedOut: true, url: location.href});
    return;
  }
  const tids = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href*='purchase-detail']")) {
    const m = /[?&]t=([\w-]+)/.exec(a.getAttribute("href") || "");
    if (m && !seen.has(m[1])) { seen.add(m[1]); tids.push(m[1]); }
  }
  const hasNext = !!document.querySelector("a[href*='page=']");
  browser.runtime.sendMessage({type: "history", loggedOut: false, tids, hasNext, url: location.href});
})();
