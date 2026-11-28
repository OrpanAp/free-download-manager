#!/usr/bin/env python3
"""Zephyr Download Manager - download manager.   pip install PySide6 requests yt-dlp
Dynamic multi-segment engine, resume, queues + scheduler, Site Grabber, file-info and
progress dialogs, antivirus hook, browser integration (Chrome/Edge/Brave/Firefox extension),
video grabbing (yt-dlp), clipboard monitor, tray icon."""
import sys, os, re, json, time, threading, uuid, subprocess, platform, base64
from typing import Any
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote, urljoin, urldefrag
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests
from PySide6 import QtCore, QtGui, QtWidgets as W
import zephyr_helpers as zh

Qt = QtCore.Qt


def resource_path(name: str) -> str:
    """Path of a file shipped with the app: works from source and inside the PyInstaller .exe
    (a --onefile exe unpacks its bundled files into sys._MEIPASS, not next to this script)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


HOME = os.path.join(os.path.expanduser("~"), ".zephyrdm")
os.makedirs(HOME, exist_ok=True)
DB, CFG = os.path.join(HOME, "downloads.json"), os.path.join(HOME, "config.json")
MAIN = "Main download queue"
MIN_SPLIT = 1 << 20
CATS = {
    "Compressed": "zip rar 7z gz tar bz2 xz iso",
    "Documents": "pdf doc docx xls xlsx ppt pptx txt epub csv",
    "Music": "mp3 flac wav aac ogg m4a wma",
    "Programs": "exe msi dmg deb rpm apk appimage",
    "Video": "mp4 mkv avi mov wmv flv webm m4v ts",
}
CAT_NAMES = list(CATS) + ["General"]
DEFAULTS: dict[str, Any] = dict(
    download_dir=os.path.join(os.path.expanduser("~"), "Downloads", "Zephyr"), cat_dirs={},
    connections=8, speed_limit_kb=0, proxy="", retries=6, port=9614, clipboard=True,
    show_progress=True, show_complete=True, beep=True, av_enabled=False,
    av_cmd='"C:\\Program Files\\Windows Defender\\MpCmdRun.exe" -Scan -ScanType 3 -File "{file}"',
    capture_exts="zip rar 7z gz tar iso exe msi dmg deb rpm apk pdf doc docx xls xlsx ppt pptx "
                 "mp3 flac wav m4a mp4 mkv avi mov wmv flv webm",
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    video_height=0, max_parallel=5,
    queues={MAIN: dict(start="", stop="", days=[0, 1, 2, 3, 4, 5, 6], max=3, after="none")})
cfg: dict[str, Any] = dict(DEFAULTS)
try:
    cfg.update(json.load(open(CFG)))
except Exception:
    pass
if str(cfg.get("user_agent", "")).endswith("Zephyr/1.0"): cfg["user_agent"] = DEFAULTS["user_agent"]   # old default got blocked by some sites


def save_cfg() -> None:
    json.dump(cfg, open(CFG, "w"), indent=2)


def category(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return next((c for c, e in CATS.items() if ext in e.split()), "General")


def cat_dir(c: str) -> str:
    return cfg["cat_dirs"].get(c) or os.path.join(cfg["download_dir"], c)


def fmt_size(n: Any) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.2f} {u}"
        n /= 1024
    return ""


def fmt_time(s: float) -> str:
    s = int(s); h, r = divmod(s, 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def opener(p: str) -> None:
    try:
        if platform.system() == "Windows": getattr(os, "startfile")(p)
        elif platform.system() == "Darwin": subprocess.Popen(["open", p])
        else: subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


class Limiter:
    def __init__(self) -> None:
        self.lock, self.t, self.sent = threading.Lock(), time.time(), 0

    def consume(self, n: int) -> None:
        lim = cfg["speed_limit_kb"] * 1024
        if lim <= 0: return
        with self.lock:
            now = time.time()
            if now - self.t >= 1: self.t, self.sent = now, 0
            self.sent += n
            if self.sent > lim:
                time.sleep(max(0, 1 - (now - self.t))); self.t, self.sent = time.time(), 0


limiter = Limiter()
KEYS = ("id url name folder size status segments resumable referer cookie user pwd connections "
        "added error kind queue desc last_try ua alts cookies").split()


class Item:
    def __init__(self, url: str, **kw: Any) -> None:
        self.id, self.url, self.name, self.folder, self.size = uuid.uuid4().hex[:8], url, "", "", 0
        self.status, self.segments, self.resumable = "Stopped", [], False
        self.referer = self.cookie = self.user = self.pwd = self.error = self.desc = self.queue = self.ua = self.phase = ""
        self.alts, self.cookies = [], []
        self.connections, self.added, self.kind, self.last_try = cfg["connections"], time.time(), "file", 0
        self.__dict__.update(kw)
        self.stop, self.user_stop, self.gen = threading.Event(), False, 0
        self.vdone, self.speed, self._last = 0, 0.0, (0, time.time())
        self.wait = False   # started by the user but waiting for a free download slot

    def done(self) -> int:
        return self.vdone if self.kind == "video" else sum(s[2] for s in self.segments)

    def pct(self) -> float:
        return self.done() * 100 / self.size if self.size else 0

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in KEYS}

    @property
    def path(self) -> str:
        return os.path.join(self.folder, self.name)


def session(it: Item) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = it.ua or cfg["user_agent"]
    if it.referer: s.headers["Referer"] = it.referer
    if it.cookie: s.headers["Cookie"] = it.cookie
    if it.user: s.auth = (it.user, it.pwd)
    if cfg["proxy"]: s.proxies = {"http": cfg["proxy"], "https": cfg["proxy"]}
    return s


def unique(folder: str, name: str) -> str:
    base, ext = os.path.splitext(name); p, i = os.path.join(folder, name), 1
    while os.path.exists(p) or os.path.exists(p + ".part"):
        p, i = os.path.join(folder, f"{base} ({i}){ext}"), i + 1
    return os.path.basename(p)


def probe(it: Item, s: requests.Session) -> None:
    r = s.get(it.url, stream=True, timeout=20, allow_redirects=True, headers={"Range": "bytes=0-0"})
    r.close()
    if r.status_code >= 400 and r.status_code != 416: r.raise_for_status()
    it.name = zh.guess_name(r.url, r.headers, it.name)          # never just "download": uses headers + Content-Type
    if zh.looks_like_html_page(r.headers, it.name):
        raise IOError("The server sent a web page instead of a file. The link may have expired or needs you to be "
                      "logged in - use the browser extension so your login cookies are sent.")
    cr = r.headers.get("Content-Range", "")
    if r.status_code == 206 and "/" in cr and cr.split("/")[1].isdigit():
        it.size, it.resumable = int(cr.split("/")[1]), True
    else:
        it.size, it.resumable = int(r.headers.get("Content-Length", 0) or 0), False
    it.url = r.url


def run_file(it: Item, ev: threading.Event) -> None:
    s = session(it)
    if it.segments and not os.path.exists(it.path + ".part"): it.segments = []
    if not it.segments:
        probe(it, s)
        if ev.is_set(): return
        it.folder = it.folder or cat_dir(category(it.name))
        os.makedirs(it.folder, exist_ok=True)
        it.name = unique(it.folder, it.name)
        n = max(1, min(it.connections, 32)) if it.resumable and it.size > MIN_SPLIT else 1
        if n == 1: it.segments = [[0, it.size - 1 if it.size else -1, 0]]
        else:
            step = it.size // n
            it.segments = [[i * step, (i + 1) * step - 1 if i < n - 1 else it.size - 1, 0] for i in range(n)]
        with open(it.path + ".part", "wb") as f:
            if it.size: f.truncate(it.size)
    part, lock, busy, failed = it.path + ".part", threading.Lock(), set(), []

    def fetch(sg: list) -> None:
        tries = 0
        while not ev.is_set():
            pos = sg[0] + sg[2]
            if it.resumable and pos > sg[1]: return
            h = {"Range": f"bytes={pos}-{sg[1]}"} if it.resumable else {}
            r = None
            try:
                r = s.get(it.url, headers=h, stream=True, timeout=25); r.raise_for_status()
                if it.resumable and r.status_code != 206: raise IOError("Server ignored Range request")
                with open(part, "r+b") as f:
                    f.seek(pos)
                    for chunk in r.iter_content(65536):
                        if ev.is_set(): return
                        if it.resumable:
                            rem = sg[1] - (sg[0] + sg[2]) + 1   # sg[1] may shrink when another worker steals half
                            if rem <= 0: return
                            chunk = chunk[:rem]
                        limiter.consume(len(chunk)); f.write(chunk); sg[2] += len(chunk); tries = 0
                if not it.resumable or sg[0] + sg[2] > sg[1]: return
            except Exception as e:
                tries += 1
                if tries > cfg["retries"]:
                    failed.append(str(e)); ev.set(); return
                time.sleep(min(2 * tries, 10))
            finally:
                if r is not None: r.close()

    def take() -> Any:
        with lock:
            for sg in it.segments:
                if id(sg) not in busy and sg[0] + sg[2] <= sg[1]:
                    busy.add(id(sg)); return sg
            best, rb = None, 0
            for sg in it.segments:   # dynamic segmentation: split the biggest remaining segment
                rem = sg[1] - (sg[0] + sg[2]) + 1
                if id(sg) in busy and rem > rb: best, rb = sg, rem
            if best is not None and rb >= 2 * MIN_SPLIT:
                mid = best[0] + best[2] + rb // 2
                new = [mid, best[1], 0]; best[1] = mid - 1
                it.segments.append(new); busy.add(id(new)); return new
        return None

    def worker() -> None:
        while not ev.is_set():
            sg = take()
            if sg is None: return
            fetch(sg)
            with lock: busy.discard(id(sg))

    n = 1 if not it.resumable else max(1, min(it.connections, 32))
    ts = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    if failed: raise IOError(failed[0])
    if not ev.is_set():
        os.replace(part, it.path)
        if zh.file_is_html(it.path):
            try: os.remove(it.path)
            except OSError: pass
            raise IOError("The server returned a web page instead of the file (expired link or login needed). "
                          "Nothing was saved - open the page in your browser while logged in and send it again.")


class _YLog:
    """yt-dlp logger: keeps warnings for diagnostics and never writes to a missing console (--noconsole exe)."""
    def __init__(self) -> None: self.msgs: list[str] = []
    def debug(self, m: Any) -> None: pass
    def info(self, m: Any) -> None: pass
    def warning(self, m: Any) -> None: self.msgs.append(str(m))
    def error(self, m: Any) -> None: self.msgs.append(str(m))


def run_video(it: Item, ev: threading.Event) -> None:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, DownloadError
    zh.setup_runtime_path()
    it.folder = it.folder or cat_dir("Video"); os.makedirs(it.folder, exist_ok=True)
    it.phase = "Preparing..."
    ff, log = zh.find_ffmpeg(resource_path("")), _YLog()
    prog: dict = {}

    def hook(d: Any) -> None:
        if ev.is_set(): raise DownloadCancelled()
        key = d.get("filename") or d.get("tmpfilename") or ""
        done, tot = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if d.get("status") == "finished": tot = done or tot
        prog[key] = (done, tot)
        info = d.get("info_dict") or {}
        sizes = [(f.get("filesize") or f.get("filesize_approx") or 0) for f in (info.get("requested_formats") or [])]
        expect = sum(sizes) if sizes and all(sizes) else 0           # video+audio total, so % does not jump back
        it.vdone = int(sum(v[0] for v in prog.values()))
        it.size = int(max(expect, sum(v[1] for v in prog.values())) or it.size)
        it.phase = "Downloading"
        if info.get("title") and not it.name: it.name = zh.safe_name(str(info["title"])) + ".mp4"

    def pp_hook(d: Any) -> None:
        if d.get("status") == "started": it.phase = "Merging..."

    cookiefile = ""
    txt = zh.netscape_cookies(it.cookies, it.cookie, it.referer or it.url)
    if txt:
        cookiefile = os.path.join(HOME, f"cookies-{it.id}.txt")
        with open(cookiefile, "w", encoding="utf-8") as f: f.write(txt)

    hdr = {"User-Agent": it.ua or cfg["user_agent"]}
    if it.referer: hdr["Referer"] = it.referer
    if it.user: hdr["Authorization"] = "Basic " + base64.b64encode(f"{it.user}:{it.pwd}".encode()).decode()

    def attempt(url: str, fmt: str) -> str:
        o: Any = {"outtmpl": os.path.join(it.folder, "%(title|video).120s.%(ext)s"), "format": fmt,
                  "progress_hooks": [hook], "postprocessor_hooks": [pp_hook], "logger": log, "quiet": True,
                  "no_warnings": True, "noprogress": True, "noplaylist": True, "continuedl": True,
                  "windowsfilenames": True, "http_headers": hdr, "retries": cfg["retries"],
                  "fragment_retries": cfg["retries"], "extractor_retries": 3,
                  "concurrent_fragment_downloads": max(1, min(int(cfg["connections"]), 8))}
        if ff: o.update(ffmpeg_location=ff, merge_output_format="mp4")
        if cfg["proxy"]: o["proxy"] = cfg["proxy"]
        if cookiefile: o["cookiefile"] = cookiefile
        with yt_dlp.YoutubeDL(o) as y:
            return zh.final_path(y, y.extract_info(url, download=True))

    last: Any = None
    try:
        for url in [it.url] + [u for u in (it.alts or []) if u and u != it.url]:    # page first, then sniffed streams
            for fmt in zh.video_formats(bool(ff), int(cfg.get("video_height") or 0)):  # H.264+AAC first, then anything
                if ev.is_set(): return
                prog.clear(); it.vdone = 0
                try:
                    path = attempt(url, fmt)
                except DownloadCancelled:
                    return
                except DownloadError as e:
                    last = e
                    if ev.is_set(): return
                    if zh.format_error(str(e)): continue      # other format selector
                    break                                      # other error: try the next URL
                if not path:
                    last = IOError("The download finished but the output file could not be found.")
                    continue
                name = os.path.basename(path)
                if zh.is_partial_stream(name):
                    raise IOError(f"Only a partial stream was saved ({name}): video and audio were not merged. "
                                  "Check that ffmpeg.exe is in the 'runtime' folder next to Zephyr.exe.")
                it.name, it.folder = name, os.path.dirname(path)
                it.size = it.vdone = os.path.getsize(path)
                it.phase = ""
                return
        if last is not None:
            raise IOError(zh.friendly_error(str(last), it.url, " ".join(log.msgs), bool(ff), zh.has_js_runtime()))
        raise IOError("Nothing could be downloaded from this address.")
    finally:
        if cookiefile:
            try: os.remove(cookiefile)
            except OSError: pass


class Manager(QtCore.QObject):
    changed, completed, notice = QtCore.Signal(), QtCore.Signal(str), QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.items: list[Item] = []; self.running: set[str] = set(); self._min = ""; self._ran: set[str] = set()
        try:
            for d in json.load(open(DB)):
                it = Item(d["url"]); it.__dict__.update(d)
                if it.status == "Downloading" or (it.status == "Queued" and not it.queue): it.status = "Stopped"
                self.items.append(it)
        except Exception:
            pass
        self.t = QtCore.QTimer(self); self.t.timeout.connect(self.tick); self.t.start(1000)

    def save(self) -> None:
        try: json.dump([i.to_json() for i in self.items], open(DB, "w"))
        except Exception: pass

    def by_id(self, i: str) -> Any:
        return next((x for x in self.items if x.id == i), None)

    def add(self, url: str, **kw: Any) -> Item:
        it = Item(url, **kw); self.items.append(it); self.save(); return it

    def active(self, q: Any = None) -> list[Item]:
        return [i for i in self.items if i.status == "Downloading" and (q is None or i.queue == q)]

    def slots(self) -> int:
        """Free download slots (Options > Connection > Max simultaneous downloads; 0 = unlimited)."""
        cap = int(cfg.get("max_parallel") or 0)
        return 1 << 30 if cap <= 0 else cap - len(self.active())

    def start(self, it: Item) -> None:
        if it.status == "Downloading": return
        if self.slots() <= 0:                      # all slots busy: wait in line, tick() starts it automatically
            it.status, it.wait, it.error = "Queued", True, ""; return
        it.wait = False
        it.user_stop, it.error, it.status, it.phase = False, "", "Downloading", ""
        it.stop = ev = threading.Event(); it.gen += 1; gen = it.gen

        def work() -> None:
            st, err = "Complete", ""
            try:
                (run_video if it.kind == "video" else run_file)(it, ev)
                if ev.is_set(): st = "Stopped"
            except Exception as e:
                st, err = ("Stopped", "") if it.user_stop else ("Error", str(e))
            if it.gen == gen:
                it.status, it.error, it.last_try, it.phase = st, err, time.time(), ""
                if st == "Complete": self.finished(it)
            self.save()
        threading.Thread(target=work, daemon=True).start()

    def finished(self, it: Item) -> None:
        self.completed.emit(it.id)
        if cfg["av_enabled"] and cfg["av_cmd"]:
            def scan() -> None:
                rc = subprocess.call(cfg["av_cmd"].replace("{file}", it.path), shell=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if rc != 0:
                    it.error = f"Antivirus reported a problem (code {rc})"
                    self.notice.emit(f"{it.name}: {it.error}")
            threading.Thread(target=scan, daemon=True).start()

    def stop(self, it: Item) -> None:
        if it.status in ("Downloading", "Queued"):
            it.user_stop = True; it.stop.set(); it.status = "Stopped"; it.wait = False

    def resume(self, it: Item) -> None:
        if it.status in ("Stopped", "Error", "Queued"): self.start(it)     # Queued: start it now, do not wait for the queue

    def remove(self, it: Item, delete_file: bool = False) -> None:
        it.user_stop = True; it.stop.set(); time.sleep(0.2)
        if delete_file:
            for p in (it.path, it.path + ".part"):
                try: os.remove(p)
                except Exception: pass
        if it in self.items: self.items.remove(it)
        self.save()

    def start_queue(self, name: str) -> None:
        for i in self.items:
            if i.queue == name and i.status in ("Stopped", "Error"): i.status = "Queued"
        self.running.add(name); self._ran.discard(name)

    def stop_queue(self, name: str) -> None:
        self.running.discard(name)
        for i in self.items:
            if i.queue == name: self.stop(i)

    def tick(self) -> None:
        now = time.time()
        for i in self.items:
            d = i.done(); i.speed = max(0.0, (d - i._last[0]) / max(0.1, now - i._last[1])); i._last = (d, now)
        lt = time.localtime(now); minute = time.strftime("%Y%m%d%H%M", lt); hm = time.strftime("%H:%M", lt)
        if minute != self._min:
            self._min = minute
            for name, q in cfg["queues"].items():
                if lt.tm_wday in q.get("days", []):
                    if q.get("start") == hm: self.start_queue(name)
                    if q.get("stop") == hm: self.stop_queue(name)
        for i in sorted(self.items, key=lambda x: x.added):          # start items that were waiting for a free slot
            if self.slots() <= 0: break
            if i.status == "Queued" and i.wait: self.start(i)
        for name in list(self.running):
            q = cfg["queues"].get(name)
            if q is None: self.running.discard(name); continue
            slots = min(int(q["max"]) - len(self.active(name)), self.slots())
            for i in self.items:
                if slots > 0 and i.queue == name and i.status == "Queued" and not i.wait: self.start(i); slots -= 1; self._ran.add(name)
            if not any(i.queue == name and i.status in ("Queued", "Downloading") for i in self.items):
                self.running.discard(name)
                if name in self._ran: self._ran.discard(name); self.queue_done(q.get("after", "none"))
        self.changed.emit()

    def queue_done(self, action: str) -> None:
        if action == "close": W.QApplication.quit()
        elif action == "shutdown":
            os.system("shutdown /s /t 30" if platform.system() == "Windows" else "shutdown -h +1")


class Bridge(QtCore.QObject):
    got = QtCore.Signal(dict)


def start_server(bridge: Bridge) -> None:
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            for k in ("Origin", "Headers", "Methods"): self.send_header(f"Access-Control-Allow-{k}", "*")
            self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def do_OPTIONS(self): self._send(204)
        def do_GET(self):
            if self.path.startswith("/config"):
                self._send(200, json.dumps({"exts": cfg["capture_exts"].lower().split()}).encode())
            else: self._send(200, b'"Zephyr"')
        def do_POST(self):
            try:
                bridge.got.emit(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
                self._send(200, b"{}")
            except Exception: self._send(400)
        def log_message(self, *a: Any) -> None: pass
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", int(cfg["port"])), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        pass


# ───────────────────────────── dialogs ─────────────────────────────
class AddUrlDialog(W.QDialog):
    def __init__(self, parent: Any, url: str = "") -> None:
        super().__init__(parent); self.setWindowTitle("Enter new address to download"); self.resize(520, 10)
        f = W.QFormLayout(self)
        self.url = W.QLineEdit(url); self.url.setPlaceholderText("https://...")
        self.auth = W.QCheckBox("Use authorization"); self.user, self.pwd = W.QLineEdit(), W.QLineEdit()
        self.pwd.setEchoMode(W.QLineEdit.EchoMode.Password); self.ref, self.cookie = W.QLineEdit(), W.QLineEdit()
        self.video = W.QCheckBox("Video page (grab with yt-dlp: YouTube, etc.)")
        for lab, w in [("Address", self.url), ("", self.auth), ("User", self.user), ("Password", self.pwd),
                       ("Referer", self.ref), ("Cookie", self.cookie), ("", self.video)]: f.addRow(lab, w)
        bb = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); f.addRow(bb)


class FileInfoDialog(W.QDialog):
    probed = QtCore.Signal(object)

    def __init__(self, parent: Any, url: str, referer: str = "", cookie: str = "", user: str = "", pwd: str = "",
                 kind: str = "file", name: str = "", ua: str = "", alts: Any = None, cookies: Any = None) -> None:
        super().__init__(parent); self.setWindowTitle("Download File Info"); self.resize(560, 10)
        self.url, self.kind, self.start_now = url, kind, False
        self.ctx = dict(referer=referer, cookie=cookie, user=user, pwd=pwd, ua=ua, alts=list(alts or []), cookies=list(cookies or []))
        f = W.QFormLayout(self)
        u = W.QLineEdit(url); u.setReadOnly(True)
        self.cat = W.QComboBox(); self.cat.addItems(CAT_NAMES)
        self.folder, self.name, self.desc = W.QLineEdit(), W.QLineEdit(name), W.QLineEdit()
        br = W.QPushButton("..."); br.setFixedWidth(30)
        br.clicked.connect(lambda: self.folder.setText(W.QFileDialog.getExistingDirectory(self, "Save to", self.folder.text()) or self.folder.text()))
        row = W.QHBoxLayout(); row.addWidget(self.folder); row.addWidget(br)
        self.info = W.QLabel("Probing server...")
        for lab, w in [("URL", u), ("Category", self.cat), ("Save As", row), ("File name", self.name),
                       ("Description", self.desc), ("", self.info)]: f.addRow(lab, w)
        self.cat.currentTextChanged.connect(lambda c: self.folder.setText(cat_dir(c)))
        self.cat.setCurrentText(category(name or url.split("?")[0]))
        self.folder.setText(cat_dir(self.cat.currentText()))
        bs = W.QHBoxLayout()
        for t, fn in [("Start Download", self.go), ("Download Later", self.later), ("Cancel", self.reject)]:
            b = W.QPushButton(t); b.clicked.connect(fn); bs.addWidget(b)
        f.addRow(bs)
        self.probed.connect(self.on_probe)
        if kind == "file": threading.Thread(target=self._probe, daemon=True).start()
        else: self.info.setText("Video page: details are resolved when the download starts.")

    def _probe(self) -> None:
        try:
            t = Item(self.url, name=self.name.text(), **self.ctx); probe(t, session(t))
            res: Any = (t.name, t.size, t.resumable, "")
        except Exception as e:
            res = ("", 0, False, str(e))
        try: self.probed.emit(res)
        except RuntimeError: pass

    def on_probe(self, r: Any) -> None:
        name, size, resumable, err = r
        if err: self.info.setText(f"Could not read file info: {err[:90]}"); return
        if not self.name.text(): self.name.setText(name)
        if name and category(name) != self.cat.currentText(): self.cat.setCurrentText(category(name))
        self.info.setText(f"Size: {fmt_size(size) if size else 'unknown'}   |   Resume: {'yes' if resumable else 'no'}")

    def go(self) -> None: self.start_now = True; self.accept()
    def later(self) -> None: self.start_now = False; self.accept()

    def data(self) -> dict:
        return dict(name=self.name.text().strip(), folder=self.folder.text().strip(), desc=self.desc.text(), kind=self.kind, **self.ctx)


class SegBar(W.QWidget):
    def __init__(self, it: Item) -> None:
        super().__init__(); self.it = it; self.setMinimumHeight(24)

    def paintEvent(self, e: Any) -> None:
        p = QtGui.QPainter(self); r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QtGui.QColor("#777")); p.setBrush(QtGui.QColor("#fff")); p.drawRect(r)
        it = self.it
        if it.size > 0:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor("#2e9e3e"))
            if it.kind == "video": p.drawRect(1, 1, int((r.width() - 1) * min(1.0, it.vdone / it.size)), r.height() - 1)
            else:
                for s in list(it.segments):
                    x = int(s[0] * r.width() / it.size) + 1
                    p.drawRect(x, 1, max(0, int(s[2] * r.width() / it.size)), r.height() - 1)
        p.end()


class StatusDialog(W.QDialog):
    def __init__(self, parent: Any, mgr: Manager, it: Item) -> None:
        super().__init__(parent); self.mgr, self.it = mgr, it; self.resize(520, 10)
        f = W.QFormLayout(self); self.l = {k: W.QLabel("") for k in ("url", "status", "size", "done", "rate", "eta", "resume")}
        self.l["url"].setText(it.url[:80])
        for lab, k in [("URL", "url"), ("Status", "status"), ("File size", "size"), ("Downloaded", "done"),
                       ("Transfer rate", "rate"), ("Time left", "eta"), ("Resume capability", "resume")]: f.addRow(lab, self.l[k])
        self.bar = SegBar(it); f.addRow(self.bar)
        self.pb = W.QProgressBar(); f.addRow(self.pb)
        bs = W.QHBoxLayout(); self.pb_btn = W.QPushButton("Pause"); self.pb_btn.clicked.connect(self.toggle)
        c = W.QPushButton("Cancel"); c.clicked.connect(lambda: (mgr.stop(it), self.close()))
        h = W.QPushButton("Hide"); h.clicked.connect(self.close)
        for b in (self.pb_btn, c, h): bs.addWidget(b)
        f.addRow(bs)
        self.t = QtCore.QTimer(self); self.t.timeout.connect(self.upd); self.t.start(400); self.upd()

    def toggle(self) -> None:
        (self.mgr.stop if self.it.status == "Downloading" else self.mgr.resume)(self.it)

    def upd(self) -> None:
        it = self.it
        self.setWindowTitle(f"{it.pct():.0f}% {it.name or 'Download'}")
        self.l["status"].setText(it.status + (f" - {it.error[:60]}" if it.error else ""))
        self.l["size"].setText(fmt_size(it.size) if it.size else "unknown")
        self.l["done"].setText(fmt_size(it.done()))
        self.l["rate"].setText(fmt_size(it.speed) + "/s" if it.status == "Downloading" else "")
        self.l["eta"].setText(fmt_time((it.size - it.done()) / it.speed) if it.speed > 1 and it.size else "")
        self.l["resume"].setText("Yes" if it.resumable else ("Yes (yt-dlp)" if it.kind == "video" else "No"))
        self.pb.setValue(int(it.pct())); self.pb_btn.setText("Pause" if it.status == "Downloading" else "Resume")
        self.bar.update()


class CompleteDialog(W.QDialog):
    def __init__(self, parent: Any, it: Item) -> None:
        super().__init__(parent); self.setWindowTitle("Download complete"); f = W.QVBoxLayout(self)
        f.addWidget(W.QLabel(f"<b>{it.name}</b><br>{fmt_size(it.size or it.done())}<br>{it.folder}"))
        bs = W.QHBoxLayout()
        for t, fn in [("Open", lambda: opener(it.path)), ("Open folder", lambda: opener(it.folder)), ("Close", self.close)]:
            b = W.QPushButton(t); b.clicked.connect(fn); bs.addWidget(b)
        f.addLayout(bs)


class OptionsDialog(W.QDialog):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent); self.setWindowTitle("Options"); self.resize(560, 420)
        v = W.QVBoxLayout(self); tabs = W.QTabWidget(); v.addWidget(tabs); self.e: dict[str, Any] = {}
        def tab(title: str, rows: list) -> None:
            w = W.QWidget(); f = W.QFormLayout(w)
            for lab, key, kind in rows:
                if kind == "bool": e: Any = W.QCheckBox(); e.setChecked(bool(cfg[key]))
                elif kind == "text": e = W.QPlainTextEdit(str(cfg[key])); e.setMaximumHeight(120)
                else: e = W.QLineEdit(str(cfg[key]))
                self.e[key] = (e, kind); f.addRow(lab, e)
            tabs.addTab(w, title)
        tab("General", [("Monitor clipboard for links", "clipboard", "bool"), ("Show progress dialog on start", "show_progress", "bool"),
                        ("Show 'Download complete' dialog", "show_complete", "bool"), ("Beep when finished", "beep", "bool")])
        tab("Save to", [("Default folder", "download_dir", "str")])
        tab("Connection", [("Max simultaneous downloads (0 = unlimited)", "max_parallel", "int"), ("Max connections per file (1-32)", "connections", "int"), ("Speed limit KB/s (0=off)", "speed_limit_kb", "int"),
                           ("Proxy (http://host:port)", "proxy", "str"), ("Retries", "retries", "int"),
                           ("User agent", "user_agent", "str"), ("Browser port (restart)", "port", "int")])
        tab("Video", [("Max video height (0 = best, e.g. 1080 or 720)", "video_height", "int")])
        tab("File types", [("Browser captures these extensions", "capture_exts", "text")])
        tab("Antivirus", [("Scan after download", "av_enabled", "bool"), ("Command ({file} = path)", "av_cmd", "str")])
        # per-category folders
        w = W.QWidget(); f = W.QFormLayout(w); self.cd = {}
        for c in CAT_NAMES: self.cd[c] = W.QLineEdit(cfg["cat_dirs"].get(c, "")); self.cd[c].setPlaceholderText(cat_dir(c)); f.addRow(c, self.cd[c])
        tabs.addTab(w, "Category folders")
        bb = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.ok); bb.rejected.connect(self.reject); v.addWidget(bb)

    def ok(self) -> None:
        for k, (e, kind) in self.e.items():
            if kind == "bool": cfg[k] = e.isChecked()
            elif kind == "text": cfg[k] = e.toPlainText()
            elif kind == "int":
                try: cfg[k] = int(e.text())
                except ValueError: pass
            else: cfg[k] = e.text()
        cfg["cat_dirs"] = {c: w.text() for c, w in self.cd.items() if w.text().strip()}
        save_cfg(); self.accept()


class SchedulerDialog(W.QDialog):
    def __init__(self, parent: Any, mgr: Manager) -> None:
        super().__init__(parent); self.mgr = mgr; self.setWindowTitle("Scheduler"); f = W.QFormLayout(self)
        self.qc = W.QComboBox(); self.qc.addItems(list(cfg["queues"])); self.cur = self.qc.currentText()
        nb, db = W.QPushButton("New queue"), W.QPushButton("Delete queue")
        row = W.QHBoxLayout(); [row.addWidget(x) for x in (self.qc, nb, db)]; f.addRow("Queue", row)
        self.sc, self.st = W.QCheckBox("Start download at"), W.QTimeEdit(); self.xc, self.xt = W.QCheckBox("Stop download at"), W.QTimeEdit()
        for t in (self.st, self.xt): t.setDisplayFormat("HH:mm")
        f.addRow(self.sc, self.st); f.addRow(self.xc, self.xt)
        dr = W.QHBoxLayout(); self.days = []
        for n in "Mon Tue Wed Thu Fri Sat Sun".split(): c = W.QCheckBox(n); self.days.append(c); dr.addWidget(c)
        f.addRow("Days", dr)
        self.mx = W.QSpinBox(); self.mx.setRange(1, 20); self.af = W.QComboBox(); self.af.addItems(["none", "close", "shutdown"])
        f.addRow("Max simultaneous downloads", self.mx); f.addRow("When done", self.af)
        nowb = W.QHBoxLayout(); a, b = W.QPushButton("Start queue now"), W.QPushButton("Stop queue now"); nowb.addWidget(a); nowb.addWidget(b); f.addRow(nowb)
        bb = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.ok); bb.rejected.connect(self.reject); f.addRow(bb)
        self.load(self.cur); self.qc.currentTextChanged.connect(self.switch)
        nb.clicked.connect(self.new); db.clicked.connect(self.delete)
        a.clicked.connect(lambda: (self.store(), mgr.start_queue(self.cur))); b.clicked.connect(lambda: mgr.stop_queue(self.cur))

    def load(self, name: str) -> None:
        q = cfg["queues"][name]
        self.sc.setChecked(bool(q["start"])); self.st.setTime(QtCore.QTime.fromString(q["start"] or "00:00", "HH:mm"))
        self.xc.setChecked(bool(q["stop"])); self.xt.setTime(QtCore.QTime.fromString(q["stop"] or "00:00", "HH:mm"))
        for i, c in enumerate(self.days): c.setChecked(i in q["days"])
        self.mx.setValue(int(q["max"])); self.af.setCurrentText(q.get("after", "none"))

    def store(self) -> None:
        if self.cur not in cfg["queues"]: return
        cfg["queues"][self.cur] = dict(start=self.st.time().toString("HH:mm") if self.sc.isChecked() else "",
                                       stop=self.xt.time().toString("HH:mm") if self.xc.isChecked() else "",
                                       days=[i for i, c in enumerate(self.days) if c.isChecked()], max=self.mx.value(), after=self.af.currentText())

    def switch(self, name: str) -> None:
        self.store(); self.cur = name
        if name: self.load(name)

    def new(self) -> None:
        n, ok = W.QInputDialog.getText(self, "New queue", "Queue name:")
        if ok and n and n not in cfg["queues"]:
            self.store(); cfg["queues"][n] = dict(start="", stop="", days=list(range(7)), max=3, after="none")
            self.qc.addItem(n); self.qc.setCurrentText(n)

    def delete(self) -> None:
        if self.cur == MAIN: return
        cfg["queues"].pop(self.cur, None); i = self.qc.currentIndex()
        for it in self.mgr.items:
            if it.queue == self.cur: it.queue = ""
        self.cur = MAIN; self.qc.removeItem(i)

    def ok(self) -> None: self.store(); save_cfg(); self.accept()


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        d = dict(attrs)
        for k in ("href", "src", "data-src"):
            v = d.get(k)
            if v and not v.startswith(("javascript:", "mailto:", "#", "data:")): self.links.append(v)


class GrabberDialog(W.QDialog):
    found, state = QtCore.Signal(str), QtCore.Signal(str)

    def __init__(self, parent: Any, mgr: Manager) -> None:
        super().__init__(parent); self.mgr, self.halt = mgr, threading.Event(); self.setWindowTitle("Site Grabber"); self.resize(640, 520)
        v = W.QVBoxLayout(self); f = W.QFormLayout()
        self.url, self.depth, self.types = W.QLineEdit(), W.QSpinBox(), W.QLineEdit("jpg png zip pdf mp4 mp3")
        self.depth.setRange(0, 4); self.depth.setValue(1); self.same = W.QCheckBox("Stay on the same site"); self.same.setChecked(True)
        f.addRow("Start page", self.url); f.addRow("Depth (levels)", self.depth); f.addRow("File types ('all' = any)", self.types); f.addRow("", self.same)
        v.addLayout(f)
        self.go_b = W.QPushButton("Start exploring"); self.go_b.clicked.connect(self.run); v.addWidget(self.go_b)
        self.lst = W.QListWidget(); v.addWidget(self.lst); self.lbl = W.QLabel(""); v.addWidget(self.lbl)
        bs = W.QHBoxLayout()
        for t, fn in [("Select all", lambda: self.mark(True)), ("Select none", lambda: self.mark(False)), ("Add to queue", self.add_sel)]:
            b = W.QPushButton(t); b.clicked.connect(fn); bs.addWidget(b)
        v.addLayout(bs); self.found.connect(self.on_found); self.state.connect(self.lbl.setText)

    def mark(self, on: bool) -> None:
        for i in range(self.lst.count()): self.lst.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def on_found(self, u: str) -> None:
        it = W.QListWidgetItem(u); it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable); it.setCheckState(Qt.CheckState.Checked); self.lst.addItem(it)

    def run(self) -> None:
        self.lst.clear(); self.halt.clear(); self.go_b.setEnabled(False)
        threading.Thread(target=self.crawl, args=(self.url.text().strip(), self.depth.value(), self.types.text().lower().split(), self.same.isChecked()), daemon=True).start()

    def crawl(self, start: str, maxd: int, types: list[str], same: bool) -> None:
        seen, out, todo, host, pages = {start}, set(), [(start, 0)], urlparse(start).netloc, 0
        s = requests.Session(); s.headers["User-Agent"] = cfg["user_agent"]
        pagelike = {"", "html", "htm", "php", "asp", "aspx", "jsp"}
        while todo and pages < 200 and not self.halt.is_set():
            url, d = todo.pop(0); pages += 1; self.state.emit(f"Exploring {pages}: {url[:80]}  | found {len(out)}")
            try:
                r = s.get(url, timeout=15)
                if "html" not in r.headers.get("Content-Type", ""): continue
                p = LinkParser(); p.feed(r.text)
            except Exception:
                continue
            for l in p.links:
                u = urldefrag(urljoin(r.url, l))[0]
                if not u.startswith("http") or u in seen: continue
                seen.add(u); ext = os.path.splitext(urlparse(u).path)[1].lstrip(".").lower()
                if ext not in pagelike and (types == ["all"] or ext in types):
                    out.add(u); self.found.emit(u)
                elif ext in pagelike and d < maxd and (not same or urlparse(u).netloc == host): todo.append((u, d + 1))
        self.state.emit(f"Done. {len(out)} files found in {pages} pages.")
        try: self.go_b.setEnabled(True)
        except RuntimeError: pass

    def add_sel(self) -> None:
        n = 0
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                x = self.mgr.add(it.text(), queue=MAIN, status="Queued", referer=self.url.text().strip())
                x.name = unquote(os.path.basename(urlparse(x.url).path)) or "download"; n += 1
        self.state.emit(f"{n} files added to '{MAIN}'. Press Start Queue.")

    def closeEvent(self, e: Any) -> None: self.halt.set(); super().closeEvent(e)


# ───────────────────────────── main window ─────────────────────────────
COLS = ["File name", "Q", "Size", "Status", "Time left", "Transfer rate", "Last try date", "Description"]


class Main(W.QMainWindow):
    def __init__(self) -> None:
        super().__init__(); self.setWindowTitle("Zephyr Download Manager"); self.resize(1150, 620)
        self.m = Manager(); self.flt: tuple = ("all", ""); self.rowids: list[str] = []; self._shown: list[str] = []
        self.dlgs: dict[str, StatusDialog] = {}; self.last_clip = ""
        sp = W.QSplitter(); self.setCentralWidget(sp)
        self.tree = W.QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setMaximumWidth(210); self.build_tree()
        self.tree.currentItemChanged.connect(lambda cur, _: (setattr(self, "flt", cur.data(0, Qt.ItemDataRole.UserRole) or ("all", "")), self.refresh()) if cur else None)
        self.table = W.QTableWidget(0, len(COLS)); self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True); self.table.setShowGrid(False)
        for i, w in enumerate([330, 24, 90, 80, 80, 100, 130, 200]): self.table.setColumnWidth(i, w)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.ctx_menu); self.table.doubleClicked.connect(self.dbl)
        sp.addWidget(self.tree); sp.addWidget(self.table); sp.setStretchFactor(1, 1)
        st = self.style(); SP = W.QStyle.StandardPixmap
        acts: list[Any] = [("Add URL", SP.SP_ArrowDown, self.add_url), ("Resume", SP.SP_MediaPlay, lambda: self.each(self.m.resume)),
                           ("Stop", SP.SP_MediaStop, lambda: self.each(self.m.stop)), ("Stop All", SP.SP_BrowserStop, lambda: [self.m.stop(i) for i in self.m.items]),
                           ("Delete", SP.SP_TrashIcon, self.delete), ("Delete C.", SP.SP_DialogDiscardButton, self.delete_completed),
                           ("Options", SP.SP_FileDialogDetailedView, lambda: OptionsDialog(self).exec()),
                           ("Scheduler", SP.SP_BrowserReload, lambda: SchedulerDialog(self, self.m).exec()),
                           ("Start Queue", SP.SP_MediaSeekForward, lambda: self.m.start_queue(self.cur_queue())),
                           ("Stop Queue", SP.SP_MediaPause, lambda: self.m.stop_queue(self.cur_queue())),
                           ("Grabber", SP.SP_DirIcon, lambda: GrabberDialog(self, self.m).show())]
        tb = self.addToolBar("main"); tb.setMovable(False); tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); tb.setIconSize(QtCore.QSize(28, 28))
        for t, ic, fn in acts: tb.addAction(st.standardIcon(ic), t, fn)
        mb = self.menuBar()
        mt = mb.addMenu("Tasks"); mt.addAction("Add new download...", self.add_url); mt.addAction("Add batch download from clipboard", self.batch_clip)
        mt.addAction("Site Grabber...", lambda: GrabberDialog(self, self.m).show()); mt.addSeparator(); mt.addAction("Exit", W.QApplication.quit)
        md = mb.addMenu("Downloads"); md.addAction("Pause all", lambda: [self.m.stop(i) for i in self.m.items]); md.addAction("Delete completed", self.delete_completed)
        md.addAction("Speed limiter...", self.speed); md.addAction("Options...", lambda: OptionsDialog(self).exec())
        mb.addMenu("Help").addAction("About", lambda: W.QMessageBox.about(self, "Zephyr", "Zephyr Download Manager 1.0 - open-source download manager."))
        self.lbl = W.QLabel(); self.statusBar().addPermanentWidget(self.lbl)
        icon_path = resource_path("icon.ico")
        app_icon = QtGui.QIcon(icon_path) if os.path.exists(icon_path) else st.standardIcon(SP.SP_ArrowDown)
        self.setWindowIcon(app_icon)
        self.tray = W.QSystemTrayIcon(app_icon, self); tm = W.QMenu()
        tm.addAction("Show", self.showNormal); tm.addAction("Quit", W.QApplication.quit); self.tray.setContextMenu(tm); self.tray.show()
        self.tray.activated.connect(lambda *_: self.showNormal())
        self.m.changed.connect(self.refresh); self.m.completed.connect(self.on_complete); self.m.notice.connect(lambda t: self.tray.showMessage("Zephyr", t))
        self.bridge = Bridge(); self.bridge.got.connect(self.from_browser); start_server(self.bridge)
        W.QApplication.clipboard().dataChanged.connect(self.clip_changed)
        W.QApplication.instance().aboutToQuit.connect(self.m.save)
        self.refresh()

    def build_tree(self) -> None:
        self.tree.clear()
        def node(parent: Any, text: str, data: tuple) -> Any:
            n = W.QTreeWidgetItem(parent, [text]); n.setData(0, Qt.ItemDataRole.UserRole, data); return n
        allr = node(self.tree, "All Downloads", ("all", ""))
        for c in CAT_NAMES: node(allr, c, ("cat", c))
        node(self.tree, "Unfinished", ("unf", "")); node(self.tree, "Finished", ("fin", ""))
        q = node(self.tree, "Queues", ("anyq", ""))
        for n in cfg["queues"]: node(q, n, ("q", n))
        self.tree.expandAll(); self.tree.setCurrentItem(allr)

    def cur_queue(self) -> str:
        return self.flt[1] if self.flt[0] == "q" else MAIN

    def vis(self) -> list[Item]:
        k, v = self.flt
        def ok(i: Item) -> bool:
            return (k == "all" or (k == "cat" and category(i.name) == v) or (k == "unf" and i.status != "Complete") or (k == "fin" and i.status == "Complete")
                    or (k == "q" and i.queue == v) or (k == "anyq" and bool(i.queue)))
        return [i for i in self.m.items if ok(i)]

    def refresh(self) -> None:
        v = self.vis(); ids = [i.id for i in v]; sel = set(self.sel_ids()); self.rowids = ids
        self.table.setRowCount(len(v))
        for r, i in enumerate(v):
            p = i.pct()
            st = i.phase if (i.status == "Downloading" and i.phase in ("Preparing...", "Merging...")) else "Complete" if i.status == "Complete" else (f"{p:.2f}%" if i.size and i.status in ("Downloading", "Stopped") and p > 0 else i.status)
            if i.error: st = "Error"
            eta = fmt_time((i.size - i.done()) / i.speed) if i.status == "Downloading" and i.speed > 1 and i.size else ""
            cells = [i.name or i.url, "●" if i.queue else "", fmt_size(i.size) if i.size else "", st, eta,
                     fmt_size(i.speed) + "/s" if i.status == "Downloading" else "",
                     time.strftime("%b %d %H:%M", time.localtime(i.last_try or i.added)), i.error or i.desc]
            for c, t in enumerate(cells):
                cell = self.table.item(r, c)
                if cell is None: cell = W.QTableWidgetItem(); self.table.setItem(r, c, cell)
                if cell.text() != t: cell.setText(t)
        if ids != self._shown:
            self._shown = ids
            for r, i in enumerate(ids):
                if i in sel: self.table.selectRow(r)
        a = self.m.active()
        self.lbl.setText(f"{len(a)} active | {fmt_size(sum(i.speed for i in a))}/s | limit: " + (f"{cfg['speed_limit_kb']} KB/s" if cfg["speed_limit_kb"] else "off"))

    def sel_ids(self) -> list[str]:
        return [self.rowids[r] for r in sorted({x.row() for x in self.table.selectedIndexes()}) if r < len(self.rowids)]

    def selected(self) -> list[Item]:
        return [x for x in (self.m.by_id(i) for i in self.sel_ids()) if x]

    def each(self, fn: Any) -> None:
        for i in self.selected(): fn(i)

    # ---- adding ----
    def add_url(self, url: str = "") -> None:
        d = AddUrlDialog(self, url if isinstance(url, str) else "")
        if not d.exec() or not d.url.text().strip(): return
        a = d.auth.isChecked()
        self.add_flow(d.url.text().strip(), referer=d.ref.text(), cookie=d.cookie.text(), user=d.user.text() if a else "", pwd=d.pwd.text() if a else "",
                      kind="video" if d.video.isChecked() else "file")

    def add_flow(self, url: str, **ctx: Any) -> None:
        if any(i.url == url and i.status != "Complete" for i in self.m.items):
            if W.QMessageBox.question(self, "Zephyr", "This address is already in your list. Add it again?") != W.QMessageBox.StandardButton.Yes: return
        d = FileInfoDialog(self, url, **ctx)
        d.setModal(False)                       # several downloads can be added/started at the same time
        d.finished.connect(lambda res, d=d, url=url: self._file_info_done(d, res, url))
        d.show(); d.raise_(); d.activateWindow()

    def _file_info_done(self, d: Any, res: int, url: str) -> None:
        d.deleteLater()
        if not res: return
        it = self.m.add(url, **d.data())
        if d.start_now:
            self.m.start(it)
            if cfg["show_progress"] and it.status == "Downloading": self.show_status(it)
        else: it.queue, it.status = MAIN, "Queued"
        self.refresh()

    def from_browser(self, d: dict) -> None:
        self.showNormal(); self.raise_(); self.activateWindow()
        self.add_flow(d["url"], referer=d.get("referer", ""), cookie=d.get("cookie", ""), kind=d.get("kind", "file"), name=d.get("filename", ""),
                      ua=d.get("ua", ""), alts=d.get("alts") or [], cookies=d.get("cookies") or [])

    def batch_clip(self) -> None:
        urls = [u for u in W.QApplication.clipboard().text().split() if u.startswith("http")]
        for u in urls: self.m.add(u, queue=MAIN, status="Queued", name=unquote(os.path.basename(urlparse(u).path)))
        self.statusBar().showMessage(f"{len(urls)} links added to '{MAIN}'", 4000)

    def clip_changed(self) -> None:
        t = W.QApplication.clipboard().text().strip()
        ext = os.path.splitext(urlparse(t).path)[1].lstrip(".").lower()
        if cfg["clipboard"] and t != self.last_clip and re.match(r"https?://\S+$", t) and ext in cfg["capture_exts"].lower().split():
            self.last_clip = t
            if W.QMessageBox.question(self, "Zephyr", f"Download this link?\n{t[:200]}") == W.QMessageBox.StandardButton.Yes: self.add_flow(t)

    # ---- dialogs / events ----
    def show_status(self, it: Item) -> None:
        d = self.dlgs.get(it.id)
        if d is None: d = self.dlgs[it.id] = StatusDialog(self, self.m, it)
        d.show(); d.raise_()

    def on_complete(self, iid: str) -> None:
        it = self.m.by_id(iid)
        if not it: return
        d = self.dlgs.pop(iid, None)
        if d: d.close()
        if cfg["beep"]: W.QApplication.beep()
        self.tray.showMessage("Download complete", it.name)
        if cfg["show_complete"]: CompleteDialog(self, it).show()

    def dbl(self) -> None:
        for i in self.selected():
            if i.status == "Complete": opener(i.path)
            else: self.show_status(i)

    def delete(self) -> None:
        s = self.selected()
        if not s: return
        r = W.QMessageBox.question(self, "Delete", "Also delete the files from disk?", W.QMessageBox.StandardButton.Yes | W.QMessageBox.StandardButton.No | W.QMessageBox.StandardButton.Cancel)
        if r == W.QMessageBox.StandardButton.Cancel: return
        for i in s: self.m.remove(i, r == W.QMessageBox.StandardButton.Yes)
        self.refresh()

    def delete_completed(self) -> None:
        for i in [x for x in self.m.items if x.status == "Complete"]: self.m.remove(i)
        self.refresh()

    def speed(self) -> None:
        v, ok = W.QInputDialog.getInt(self, "Speed limiter", "Global limit in KB/s (0 = unlimited):", cfg["speed_limit_kb"], 0, 10**7)
        if ok: cfg["speed_limit_kb"] = v; save_cfg()

    def ctx_menu(self, pos: Any) -> None:
        m = W.QMenu()
        for t, fn in [("Open", lambda: self.each(lambda i: opener(i.path))), ("Open folder", lambda: self.each(lambda i: opener(i.folder or cfg["download_dir"]))),
                      ("Resume", lambda: self.each(self.m.resume)), ("Stop", lambda: self.each(self.m.stop)),
                      ("Show progress", lambda: self.each(self.show_status))]: m.addAction(t, fn)
        qm = m.addMenu("Move to queue")
        for n in cfg["queues"]: qm.addAction(n, lambda n=n: self.each(lambda i: self.set_queue(i, n)))
        qm.addAction("(none)", lambda: self.each(lambda i: self.set_queue(i, "")))
        m.addAction("Copy address", lambda: W.QApplication.clipboard().setText("\n".join(i.url for i in self.selected())))
        m.addAction("Redownload", lambda: self.each(self.redo)); m.addAction("Delete", self.delete)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def set_queue(self, it: Item, n: str) -> None:
        it.queue = n
        if n and it.status in ("Stopped", "Error"): it.status = "Queued"

    def redo(self, it: Item) -> None:
        self.m.stop(it); time.sleep(0.3)
        try: os.remove(it.path + ".part")
        except Exception: pass
        it.segments, it.vdone = [], 0; self.m.start(it)

    def closeEvent(self, e: Any) -> None:
        e.ignore(); self.hide(); self.tray.showMessage("Zephyr", "Still running in the system tray.")


if __name__ == "__main__":
    os.makedirs(cfg["download_dir"], exist_ok=True)
    app = W.QApplication(sys.argv); app.setQuitOnLastWindowClosed(False)
    _ic = resource_path("icon.ico")
    if os.path.exists(_ic): app.setWindowIcon(QtGui.QIcon(_ic))
    w = Main()
    if "--minimized" in sys.argv[1:]: w.hide()   # used by the "start with Windows" shortcut
    else: w.show()
    sys.exit(app.exec())
