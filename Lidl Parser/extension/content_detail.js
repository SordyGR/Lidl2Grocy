// Runs on purchase-detail pages: wait for the receipt marker, send innerText.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "detail", loggedOut: true, url: location.href});
    return;
  }
  const MARK = "ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ";
  const t0 = Date.now();
  const timer = setInterval(() => {
    const body = document.body ? document.body.innerText : "";
    if (body.includes(MARK)) {
      clearInterval(timer);
      browser.runtime.sendMessage({type: "detail", loggedOut: false, text: body});
    } else if (Date.now() - t0 > 20000) {
      clearInterval(timer);
      browser.runtime.sendMessage({type: "detail", loggedOut: false, text: "", timeout: true});
    }
  }, 500);
})();
