(() => {
  if (window.__pydm) return; window.__pydm = 1;
  const LABEL = "⬇ Download with Zephyr";
  const btn = document.createElement("div");
  btn.textContent = LABEL;
  btn.style.cssText = "position:fixed;z-index:2147483647;display:none;padding:6px 12px;background:#2e9e3e;color:#fff;" +
    "font:600 13px sans-serif;border-radius:4px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)";
  document.documentElement.appendChild(btn);
  let cur = null, hideT = 0, last = 0;
  document.addEventListener("mousemove", (e) => {
    const now = Date.now(); if (now - last < 250) return; last = now;
    const v = document.elementsFromPoint(e.clientX, e.clientY).find(x => x.tagName === "VIDEO");
    if (!v) return;
    const r = v.getBoundingClientRect();
    if (r.width < 200 || r.height < 120) return;
    cur = v; btn.style.left = Math.max(0, r.right - 190) + "px"; btn.style.top = Math.max(0, r.top + 8) + "px";
    btn.style.display = "block"; clearTimeout(hideT); hideT = setTimeout(() => btn.style.display = "none", 3000);
  }, true);
  btn.addEventListener("click", (e) => {
    e.stopPropagation(); e.preventDefault();
    const src = cur ? (cur.currentSrc || cur.src || "") : "";
    chrome.runtime.sendMessage({ cmd: "grab", src, page: location.href, title: document.title });
    btn.textContent = "✔ Sent to Zephyr"; setTimeout(() => btn.textContent = LABEL, 1500);
  });
})();
