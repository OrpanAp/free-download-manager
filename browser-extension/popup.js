chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  chrome.runtime.sendMessage({ cmd: "list", tabId: tab.id }, (list) => {
    const box = document.getElementById("l"); box.textContent = "";
    if (!list || !list.length) { box.textContent = "No media detected yet. Play the video first."; }
    (list || []).forEach(m => {
      const row = document.createElement("div"); row.className = "r";
      const u = document.createElement("span"); u.className = "u"; u.title = m.url;
      u.textContent = (m.stream ? "[stream] " : (m.size ? (m.size / 1048576).toFixed(1) + " MB  " : "")) + m.url;
      const b = document.createElement("button"); b.textContent = "Download";
      b.onclick = () => { chrome.runtime.sendMessage({ cmd: "send", payload: { url: m.url, referer: tab.url, kind: m.stream ? "video" : "file", filename: /\.[a-z0-9]{2,5}(\?|$)/i.test(m.url) ? "" : (tab.title || "") } }); b.textContent = "Sent"; };
      row.append(u, b); box.append(row);
    });
  });
  document.getElementById("page").onclick = () => chrome.runtime.sendMessage({ cmd: "grab", tabId: tab.id, page: tab.url, title: tab.title, src: "" });
});
