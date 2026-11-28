"""Zephyr helpers that do not need Qt: file naming, yt-dlp format choice, cookies, ffmpeg lookup, error text."""
import os, re, sys, shutil
from typing import Any
from urllib.parse import urlparse, unquote, parse_qs

MIME_EXT = {
    "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "video/x-flv": ".flv", "video/mp2t": ".ts", "video/x-msvideo": ".avi", "video/x-ms-wmv": ".wmv",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg",
    "audio/flac": ".flac", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/webm": ".weba",
    "application/zip": ".zip", "application/x-zip-compressed": ".zip", "application/pdf": ".pdf",
    "application/x-rar-compressed": ".rar", "application/vnd.rar": ".rar", "application/x-7z-compressed": ".7z",
    "application/gzip": ".gz", "application/x-gzip": ".gz", "application/x-tar": ".tar",
    "application/x-msdownload": ".exe", "application/vnd.microsoft.portable-executable": ".exe",
    "application/x-msi": ".msi", "application/vnd.android.package-archive": ".apk",
    "application/x-iso9660-image": ".iso", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt", "text/csv": ".csv", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp",
}
# names a server/site often gives that tell us nothing about the real file
GENERIC_NAMES = {"", "download", "downloads", "file", "get", "videoplayback", "stream", "media", "video",
                 "index", "attachment", "get_file", "dl", "play", "watch", "fetch", "redirect", "view", "open"}


def safe_name(n: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", n or "").strip(" .")
    return n[:180]


def name_from_disposition(cd: str) -> str:
    """File name from a Content-Disposition header (handles filename*=UTF-8''x and filename="x")."""
    if not cd: return ""
    m = re.search(r"filename\*\s*=\s*([\w-]*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try: return unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8")
        except Exception: return unquote(m.group(2).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    return unquote(m.group(1).strip().strip('"')) if m else ""


def _has_ext(name: str) -> bool:
    ext = os.path.splitext(name)[1]
    return bool(re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext))


def guess_name(url: str, headers: Any, hint: str = "") -> str:
    """Best file name for a download: user/browser hint > Content-Disposition > ?filename= > URL path,
    then add an extension from Content-Type when the name has none (fixes files called just 'download')."""
    name = (hint or "").strip()
    if name.lower() in GENERIC_NAMES: name = ""
    if not name: name = name_from_disposition(headers.get("Content-Disposition", ""))
    if not name:
        q = parse_qs(urlparse(url).query)
        for k in ("response-content-disposition", "filename", "file", "fn", "name", "title"):
            v = (q.get(k) or [""])[0]
            if not v: continue
            cand = name_from_disposition(v) if "filename" in v.lower() else unquote(v)
            if cand and (_has_ext(cand) or k != "title"): name = cand; break
    if not name: name = unquote(os.path.basename(urlparse(url).path))
    name = safe_name(name)
    if name.lower() in GENERIC_NAMES: name = ""
    ct = (headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
    ext = MIME_EXT.get(ct, "")
    if ext and not _has_ext(name): name = (name or "download") + ext
    return name or "download"


def looks_like_html_page(headers: Any, name: str) -> bool:
    ct = (headers.get("Content-Type", "") or "").lower()
    return ct.startswith("text/html") and not name.lower().endswith((".html", ".htm", ".php", ".asp", ".aspx"))


def file_is_html(path: str) -> bool:
    """True if a small downloaded file is really a web page (expired link / login page saved as a video)."""
    try:
        if os.path.getsize(path) > 2_000_000: return False
        with open(path, "rb") as f: head = f.read(1024).lstrip().lower()
        return head.startswith((b"<!doctype html", b"<html", b"<?xml", b"<head")) and not path.lower().endswith((".html", ".htm", ".xml", ".svg"))
    except OSError:
        return False


def video_formats(have_ffmpeg: bool, height: int = 0) -> list:
    """yt-dlp format selectors to try in order. H.264 + AAC first so the result plays in Windows Movies & TV /
    Media Player (VP9/AV1/Opus files are what give error 0xC00D36E5). Without ffmpeg only single-file formats
    are requested, because merging video+audio needs ffmpeg."""
    h = f"[height<={int(height)}]" if height and int(height) > 0 else ""
    if have_ffmpeg:
        return [f"bv*[vcodec^=avc1]{h}+ba[acodec^=mp4a]/b[ext=mp4]{h}/bv*{h}+ba/b{h}",
                f"b{h}/bv*+ba/b"]
    return [f"b[ext=mp4]{h}/b{h}", "b/w"]


def format_error(msg: str) -> bool:
    """True if a yt-dlp error means 'try a different format selector' (not a real failure)."""
    return bool(re.search(r"requested format is not available|merging of multiple formats|ffmpeg|ffprobe|"
                          r"only images are available|format.*not available", msg or "", re.I))


def friendly_error(msg: str, url: str = "", log: str = "", have_ffmpeg: bool = True, have_js: bool = True) -> str:
    raw = re.sub(r"\x1b\[[0-9;]*m", "", msg or "").replace("ERROR: ", "").strip()
    low, all_low = raw.lower(), (raw + " " + (log or "")).lower()
    youtube = bool(re.search(r"youtu(\.be|be\.com)", url or ""))
    tail = f" [{raw[:160]}]" if raw else ""
    if "drm" in low:
        return "This video is DRM-protected (Netflix, Disney+, Prime Video...) and cannot be downloaded." + tail
    if "ffmpeg" in low or "ffprobe" in low or "merging of multiple formats" in low:
        return ("Video and audio are separate and Zephyr could not merge them because ffmpeg is missing. "
                "Reinstall Zephyr (ffmpeg is included) or put ffmpeg.exe in the 'runtime' folder next to Zephyr.exe." + tail)
    if (youtube or "challenge" in all_low) and (not have_js or "javascript runtime" in all_low or "n challenge" in all_low
                                                or "only images are available" in low or "signature" in low):
        return ("YouTube needs a JavaScript runtime (Deno) to unlock the video formats. Reinstall Zephyr "
                "(Deno is included) or install Deno from deno.com, then try again." + tail)
    if re.search(r"sign in|log in|login|private video|members|http error 40[13]|confirm your age|age-restricted|not a bot", low):
        return ("The site wants you to be logged in (or blocked the request). Open the video in your browser while "
                "logged in, then use the Zephyr extension button again so your login cookies are sent." + tail)
    if "unsupported url" in low:
        return "Zephyr could not find a video on this page. Press play first, then use the extension's Download button." + tail
    if "requested format is not available" in low:
        return ("No downloadable format matched. Try Settings > Video > Max video height = 0, or another video." + tail)
    if "429" in low or "too many requests" in low:
        return "The site is rate-limiting you (too many requests). Wait a few minutes and try again." + tail
    if "not available in your country" in low or "geo" in low:
        return "This video is not available in your country." + tail
    if re.search(r"video unavailable|has been removed|no longer available|does not exist|http error 404", low):
        return "The video is unavailable or was removed." + tail
    if re.search(r"getaddrinfo|timed out|connection|network|ssl", low):
        return "Network problem while contacting the site. Check your connection/proxy and resume." + tail
    return raw[:300] or "Unknown video download error."


def netscape_cookies(cookies: list, header: str, page_url: str) -> str:
    """Cookie-jar text (Netscape format) for yt-dlp's cookiefile option, from the structured cookies the browser
    extension sent, or - as a fallback - from a plain 'a=b; c=d' Cookie header."""
    import time
    lines = ["# Netscape HTTP Cookie File"]
    soon = int(time.time()) + 86400
    for c in cookies or []:
        try:
            dom = str(c.get("domain") or "")
            if not dom: continue
            exp = int(float(c.get("expires") or 0)) or soon
            if exp < time.time(): continue
            val = str(c.get("value", "")).replace("\t", " ").replace("\n", "").replace("\r", "")
            lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", str(c.get("path") or "/"),
                                    "TRUE" if c.get("secure") else "FALSE", str(exp), str(c.get("name", "")), val]))
        except Exception:
            continue
    if len(lines) == 1 and header:
        host = (urlparse(page_url).hostname or "")
        if host:
            dom = "." + (host[4:] if host.startswith("www.") else host)
            for part in header.split(";"):
                n, _, v = part.strip().partition("=")
                if n: lines.append("\t".join([dom, "TRUE", "/", "FALSE", str(soon), n, v.replace("\t", " ")]))
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def app_dir() -> str:
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


_ff: list = []


def find_ffmpeg(extra_dir: str = "") -> str:
    """ffmpeg.exe shipped with Zephyr ('runtime' folder next to the app), else imageio-ffmpeg, else PATH."""
    if _ff: return _ff[0]
    cands = [os.environ.get("ZEPHYR_FFMPEG", "")]
    for d in (os.path.join(app_dir(), "runtime"), app_dir(), extra_dir):
        if d: cands += [os.path.join(d, "ffmpeg.exe"), os.path.join(d, "ffmpeg")]
    found = next((c for c in cands if c and os.path.isfile(c)), "")
    if not found:
        try:
            import imageio_ffmpeg
            c = imageio_ffmpeg.get_ffmpeg_exe()
            if c and os.path.isfile(c): found = c
        except Exception:
            pass
    _ff.append(found or shutil.which("ffmpeg") or "")
    return _ff[0]


def setup_runtime_path() -> None:
    """Make the bundled Deno (needed by yt-dlp for YouTube) visible to yt-dlp."""
    d = os.path.join(app_dir(), "runtime")
    if os.path.isdir(d) and d.lower() not in os.environ.get("PATH", "").lower():
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def has_js_runtime() -> bool:
    return any(shutil.which(x) for x in ("deno", "node", "qjs", "bun"))


def final_path(y: Any, info: Any) -> str:
    """The file yt-dlp really produced (after merging), not an intermediate .fNNN stream."""
    cands: list = []

    def collect(i: Any) -> None:
        if not isinstance(i, dict): return
        for rd in i.get("requested_downloads") or []:
            for k in ("filepath", "_filename", "filename"):
                if rd.get(k): cands.append(rd[k])
        for k in ("filepath", "_filename"):
            if i.get(k): cands.append(i[k])

    if isinstance(info, dict):
        for e in (info.get("entries") or []): collect(e)
        collect(info)
        try: cands.append(y.prepare_filename(info))
        except Exception: pass
    for c in cands:
        if c and os.path.isfile(c): return c
    for c in cands:   # merged output may have a different extension than the template predicted
        base = os.path.splitext(c)[0]
        for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".mov"):
            if os.path.isfile(base + ext): return base + ext
    return ""


def is_partial_stream(name: str) -> bool:
    """'Title.f137.mp4' / 'Title.f251.webm' = a single unmerged stream (video-only or audio-only)."""
    return bool(re.search(r"\.f[\w-]+\.(mp4|webm|m4a|mkv|mov)$", name or "", re.I))
