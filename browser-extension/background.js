const ZEPHYR = "http://127.0.0.1:9614";
const media = {}; // tabId -> { url: {url,type,size,stream} }
const MEDIA_RE = /\.(mp4|webm|mkv|m3u8|mpd|mp3|m4a|aac|flac|ogg|mov|flv)(\?|$)/i;
// Pieces of a stream (HLS/DASH fragments, YouTube "videoplayback" chunks). Saving one of these gives a useless file
// called "download"/"videoplayback" that will not play (Windows error 0xC00D36E5), so they are never offered.
const SEGMENT_RE = /\.(ts|m4s|cmfv|cmfa|vtt|webvtt)(\?|$)|videoplayback|googlevideo\.com|[?&](range|bytestart|sq|seg|segment|frag)=|\/(seg|segment|chunk|frag|fragment)[-_\/]?\d|init\.mp4/i;

async function getCfg() {
  try { return await (await fetch(ZEPHYR + "/config")).json(); } catch (e) { return null; }
}

// Cookies the browser would send to these URLs (video sites often need the logged-in session)
async function collectCookies(urls) {
  const seen = new Map();
  for (const u of urls.filter(x => /^https?:/i.test(x || "")).slice(0, 5)) {
    try {
      for (const c of await chrome.cookies.getAll({ url: u })) seen.set(c.domain + "|" + c.path + "|" + c.name, c);
    } catch (e) {}
  }
  return [...seen.values()].map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path,
                                        secure: !!c.secure, expires: c.expirationDate || 0 }));
}

async function send(p) {
  p.ua = navigator.userAgent;                                   // same User-Agent as the browser
  try {
    const own = await chrome.cookies.getAll({ url: p.url });
    p.cookie = own.map(c => c.name + "=" + c.value).join("; ");
    p.cookies = await collectCookies([p.url, p.referer].concat(p.alts || []));
  } catch (e) {}
  return fetch(ZEPHYR + "/add", { method: "POST", body: JSON.stringify(p) });
}

// Order streams: master/manifest playlists first, per-quality chunklists last
function rankStreams(list) {
  const score = (u) => /master|manifest|playlist|\.mpd/i.test(u) ? 0 : (/chunklist|media_|audio|subtitle/i.test(u) ? 2 : 1);
  return list.filter(x => x.stream).sort((a, b) => score(a.url) - score(b.url)).map(x => x.url);
}
function bigFiles(list) {
  return list.filter(x => !x.stream && (x.size || 0) >= 1048576).sort((a, b) => b.size - a.size).map(x => x.url);
}
const hasExt = (u) => { try { return /\.[a-z0-9]{2,5}$/i.test(new URL(u).pathname); } catch (e) { return false; } };

// 1) Take over browser downloads (falls back to the browser if Zephyr is not running)
chrome.downloads.onCreated.addListener(async (item) => {
  const c = await getCfg();
  if (!c) return;
  const url = item.finalUrl || item.url;
  if (/^(blob|data):/i.test(url)) return;
  const ext = (new URL(url).pathname.split(".").pop() || "").toLowerCase();
  const mime = item.mime || "";
  const wanted = c.exts.includes(ext) || /^(application\/(octet-stream|zip|x-)|video\/|audio\/)/.test(mime);
  if (!wanted) return;
  const bname = (item.filename || "").split(/[\\/]/).pop();   // the name the browser itself worked out
  await send({ url, referer: item.referrer || "", filename: /\.[a-z0-9]{2,5}$/i.test(bname) ? bname : "" });
  chrome.downloads.cancel(item.id, () => chrome.downloads.erase({ id: item.id }));
});

// 2) Sniff media requests per tab (video/audio/HLS/DASH) - never fragments
chrome.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0 || SEGMENT_RE.test(d.url)) return;
  const h = {};
  (d.responseHeaders || []).forEach(x => h[x.name.toLowerCase()] = x.value);
  const ct = (h["content-type"] || "").toLowerCase();
  const len = parseInt(h["content-length"] || "0", 10);
  const stream = /mpegurl|dash\+xml/.test(ct) || /\.(m3u8|mpd)(\?|$)/i.test(d.url);
  const isMedia = ct.startsWith("video/") || ct.startsWith("audio/") || stream || (MEDIA_RE.test(d.url) && !ct.includes("text/html"));
  if (!isMedia || (!stream && len && len < 100 * 1024)) return;
  const m = (media[d.tabId] = media[d.tabId] || {});
  m[d.url] = { url: d.url, type: ct, size: len, stream };
  chrome.action.setBadgeText({ tabId: d.tabId, text: String(Object.keys(m).length) });
}, { urls: ["<all_urls>"] }, ["responseHeaders"]);

chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === "loading" && info.url) { delete media[id]; chrome.action.setBadgeText({ tabId: id, text: "" }); }
});
chrome.tabs.onRemoved.addListener(id => delete media[id]);

// 3) Messages from popup / floating "Download this video" button
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = msg.tabId ?? (sender.tab && sender.tab.id);
  if (msg.cmd === "list") { reply(Object.values(media[tabId] || {})); return; }
  if (msg.cmd === "grab") {
    (async () => {
      const list = Object.values(media[tabId] || {});
      const direct = msg.src && /^https?:/i.test(msg.src) && !SEGMENT_RE.test(msg.src) ? msg.src : null;
      if (direct) {
        // a real file/stream address on the <video> tag
        await send({ url: direct, referer: msg.page, filename: hasExt(direct) ? "" : (msg.title || ""),
                     kind: /\.(m3u8|mpd)(\?|$)/i.test(direct) ? "video" : "file" });
      } else {
        // let Zephyr (yt-dlp) work on the page first; if it can't, it tries the streams we saw, best first
        await send({ url: msg.page, referer: msg.page, kind: "video",
                     alts: rankStreams(list).concat(bigFiles(list)).slice(0, 6) });
      }
      reply({ ok: true });
    })();
    return true;
  }
  if (msg.cmd === "send") { send(msg.payload).then(() => reply({ ok: true })); return true; }
});

// 4) Right-click menu
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "zephyr", title: "Download with Zephyr", contexts: ["link", "video", "audio", "image"] });
  chrome.contextMenus.create({ id: "zephyr-page", title: "Grab video from this page with Zephyr", contexts: ["page"] });
});
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "zephyr-page") {
    const l = Object.values(media[tab && tab.id] || {});
    send({ url: info.pageUrl, referer: info.pageUrl, kind: "video", alts: rankStreams(l).concat(bigFiles(l)).slice(0, 6) });
  } else send({ url: info.linkUrl || info.srcUrl, referer: tab && tab.url });
});
