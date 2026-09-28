const $ = (id) => document.getElementById(id);

async function load() {
  const s = await browser.storage.local.get({serverUrl: "", token: "", pageCap: 5});
  $("serverUrl").value = s.serverUrl;
  $("token").value = s.token;
  $("pageCap").value = s.pageCap;
}

async function save() {
  await browser.storage.local.set({
    serverUrl: $("serverUrl").value.trim().replace(/\/$/, ""),
    token: $("token").value.trim(),
    pageCap: Math.max(1, Math.min(100, parseInt($("pageCap").value, 10) || 5)),
  });
}

$("sync").addEventListener("click", async () => {
  await save();
  $("sync").disabled = true;
  $("cancel").disabled = false;
  $("status").textContent = "Starting…";
  await browser.runtime.sendMessage({type: "sync", fullScan: $("fullScan").checked});
});

$("cancel").addEventListener("click", async () => {
  await browser.runtime.sendMessage({type: "cancel"});
});

browser.runtime.onMessage.addListener((m) => {
  if (m.type === "progress") {
    $("status").textContent = m.text;
    if (m.done) { $("sync").disabled = false; $("cancel").disabled = true; }
  }
});

load();
