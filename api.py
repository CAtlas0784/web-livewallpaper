from __future__ import annotations

import asyncio
import html
import ipaddress
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import cv2
import imageio_ffmpeg
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.environ.get("LOOP_DATA_DIR", ROOT / "data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "hub.sqlite3"
MAX_MEDIA_BYTES = int(os.environ.get("LOOP_MAX_MEDIA_BYTES", str(250 * 1024 * 1024)))
SCAN_CACHE: dict[str, dict[str, Any]] = {}
SAMPLE_FPS = 6
CATALOG_BASE = os.environ.get("LOOP_CATALOG_BASE", "https://autopatch.hk4e.com/hoyoplay_background")
CATALOG_GAMES = {"genshin": "hk4e", "zzz": "nap", "hsr": "hkrpg"}
CATALOG_ASSET_HOSTS = {"launcher-webstatic.hoyoverse.com", "launcher-webstatic.mihoyo.com", "fastcdn.hoyoverse.com"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LOOP Live Wallpaper Hub API", version="0.1.0")
allowed_origins = [origin.strip() for origin in os.environ.get(
    "LOOP_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ScanRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    selector: str = Field(default="", max_length=500)
    captureSeconds: int = Field(default=12, ge=2, le=60)


class CaptureRequest(BaseModel):
    scanId: str
    candidateId: str


class CatalogImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(min_length=1, max_length=200)
    coverUrl: str = Field(default="", max_length=2048)


class MediaRequest(BaseModel):
    mediaId: str


class LibraryRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sourceUrl: str = Field(min_length=8, max_length=2048)
    mediaId: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    similarity: float = Field(ge=0, le=100)


class ExportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    mediaId: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS wallpapers (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL,
                media_id TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                similarity REAL NOT NULL, created_at TEXT NOT NULL
            )"""
        )


init_db()


def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="ใส่ URL เว็บที่ขึ้นต้นด้วย http:// หรือ https://")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(status_code=400, detail="ระบบรับเฉพาะหน้าเว็บสาธารณะ")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"หา host ของ URL ไม่ได้: {hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise HTTPException(status_code=400, detail="ระบบรับเฉพาะ URL ที่ชี้ไปยัง IP สาธารณะ")
    return url.strip()


async def browser_route_guard(route) -> None:
    request_url = route.request.url
    scheme = urlparse(request_url).scheme.lower()
    if scheme in {"data", "blob", "about"}:
        await route.continue_()
        return
    try:
        validate_public_url(request_url)
    except HTTPException:
        await route.abort()
        return
    await route.continue_()


async def scan_page(url: str, custom_selector: str = "", capture_seconds: int = 12) -> tuple[str, str, list[dict[str, Any]]]:
    url = validate_public_url(url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(ignore_https_errors=False, service_workers="block", viewport={"width": 1920, "height": 1080})
        await context.route("**/*", browser_route_guard)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            observed = await page.evaluate(
                """() => {
                  const videos = [...document.querySelectorAll('video')].map((v, index) => ({
                    index, url: v.currentSrc || v.src || v.querySelector('source')?.src || '',
                    width: v.videoWidth || v.clientWidth, height: v.videoHeight || v.clientHeight,
                    duration: Number.isFinite(v.duration) ? v.duration : null
                  }));
                  const canvases = [...document.querySelectorAll('canvas')]
                    .map((c, index) => ({ index, width: c.width, height: c.height }))
                    .filter(c => c.width >= 240 && c.height >= 135);
                  const images = [...document.querySelectorAll('img')]
                    .filter(i => i.naturalWidth >= 320 && i.naturalHeight >= 180)
                    .slice(0, 40).map(i => ({ url: i.currentSrc || i.src, width: i.naturalWidth, height: i.naturalHeight }));
                  const cssSelector = el => {
                    if (el.id) return '#' + CSS.escape(el.id);
                    const parts = []; let node = el;
                    while (node && node !== document.body && parts.length < 5) {
                      let part = node.tagName.toLowerCase();
                      const classes = [...node.classList].filter(x => x && !/^\\d/.test(x)).slice(0, 2);
                      if (classes.length) part += '.' + classes.map(x => CSS.escape(x)).join('.');
                      else if (node.parentElement) {
                        const siblings = [...node.parentElement.children].filter(x => x.tagName === node.tagName);
                        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
                      }
                      parts.unshift(part); node = node.parentElement;
                      if (node?.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
                    }
                    return parts.join(' > ');
                  };
                  const area = innerWidth * innerHeight;
                  const compositions = [...document.querySelectorAll('main,section,article,div')].map(el => {
                    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
                    const interactiveCount = el.querySelectorAll('button,a,input,select,textarea,nav,[role="button"]').length;
                    const textLength = (el.innerText || '').trim().length;
                    const hasVisual = s.backgroundImage !== 'none' || Boolean(el.querySelector('img,picture,canvas,video'));
                    return { selector:cssSelector(el), width:Math.round(r.width), height:Math.round(r.height),
                      area:r.width*r.height, position:s.position, zIndex:s.zIndex, children:el.children.length,
                      interactiveCount, textLength, hasVisual };
                  }).filter(x => x.width >= innerWidth*.55 && x.height >= innerHeight*.45 && x.area <= area*1.8 &&
                    x.children >= 1 && x.interactiveCount === 0 && x.textLength <= 80 && x.hasVisual)
                    .sort((a,b) => (b.position === 'fixed' || b.position === 'absolute') - (a.position === 'fixed' || a.position === 'absolute') || b.area-a.area).slice(0,20);
                  const mediaPattern = /\\.(mp4|webm|m4v|mov)(?:$|[?#])/i;
                  const imagePattern = /\\.(png|jpe?g|webp|avif|gif|ktx2|basis)(?:$|[?#])/i;
                  const resources = performance.getEntriesByType('resource').map(r => ({ url:r.name, kind:r.initiatorType }))
                    .filter(r => mediaPattern.test(r.url) || r.kind === 'video' || imagePattern.test(r.url) || r.kind === 'img');
                  return { title: document.title || location.hostname, videos, canvases, images, resources, compositions };
                }"""
            )
            candidates: list[dict[str, Any]] = []
            video_urls: set[str] = set()
            image_urls: set[str] = set()
            for video in observed["videos"]:
                if video["url"]:
                    candidate = {"id": uuid.uuid4().hex, "kind": "video", "label": "วิดีโอในหน้าเว็บ",
                                 "url": video["url"], "videoIndex": video["index"], "width": video["width"],
                                 "height": video["height"], "duration": video["duration"]}
                    candidates.append(candidate)
                    video_urls.add(video["url"])
            for resource in observed["resources"]:
                resource_url = resource["url"]
                is_video = resource["kind"] == "video" or any(ext in urlparse(resource_url).path.lower() for ext in (".mp4", ".webm", ".m4v", ".mov"))
                is_image = resource["kind"] == "img" or any(ext in urlparse(resource_url).path.lower() for ext in (".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".ktx2", ".basis"))
                if is_video and resource_url.startswith(("http://", "https://")) and resource_url not in video_urls:
                    candidates.append({"id": uuid.uuid4().hex, "kind": "video", "label": "วิดีโอจากเครือข่ายหน้าเว็บ",
                                       "url": resource_url, "videoIndex": None, "width": None, "height": None, "duration": None})
                    video_urls.add(resource_url)
                elif is_image and resource_url.startswith(("http://", "https://")) and resource_url not in image_urls:
                    candidates.append({"id": uuid.uuid4().hex, "kind": "image", "label": "ภาพ/Texture จากเครือข่ายหน้าเว็บ",
                                       "url": resource_url, "width": None, "height": None, "duration": None})
                    image_urls.add(resource_url)
            for canvas in observed["canvases"]:
                candidates.append({"id": uuid.uuid4().hex, "kind": "canvas", "label": "Canvas ที่หน้าเว็บเรนเดอร์",
                                   "canvasIndex": canvas["index"], "width": canvas["width"], "height": canvas["height"],
                                   "duration": capture_seconds, "captureSeconds": capture_seconds})
            selectors: set[str] = set()
            for element in observed.get("compositions", []):
                selector = element["selector"]
                if selector and selector not in selectors:
                    selectors.add(selector)
                    candidates.append({"id": uuid.uuid4().hex, "kind": "element", "label": "ฉากประกอบ DOM/Sprite",
                                       "selector": selector, "width": element["width"], "height": element["height"],
                                       "duration": capture_seconds, "captureSeconds": capture_seconds})
            if custom_selector.strip() and custom_selector.strip() not in selectors:
                selector = custom_selector.strip()
                exists = await page.evaluate("selector => { try { return Boolean(document.querySelector(selector)); } catch (_) { return false; } }", selector)
                if exists:
                    candidates.insert(0, {"id": uuid.uuid4().hex, "kind": "element", "label": "ฉากตาม CSS selector ที่ระบุ",
                                          "selector": selector, "width": None, "height": None,
                                          "duration": capture_seconds, "captureSeconds": capture_seconds})
            for image in observed["images"]:
                if image["url"].startswith(("http://", "https://")) and image["url"] not in image_urls:
                    candidates.append({"id": uuid.uuid4().hex, "kind": "image", "label": "ภาพหรือ Texture",
                                       "url": image["url"], "width": image["width"], "height": image["height"], "duration": None})
                    image_urls.add(image["url"])
            candidates.sort(key=lambda item: {"video": 0, "canvas": 1, "element": 2, "image": 3}.get(item["kind"], 4))
            return str(page.url), observed["title"], candidates[:80]
        finally:
            await context.close()
            await browser.close()


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "library": "persistent"}


def fetch_catalog(game: str, region: str) -> list[dict[str, Any]]:
    if game not in CATALOG_GAMES or region not in {"global", "cn"}:
        raise HTTPException(status_code=400, detail="เลือกเกมหรือเซิร์ฟเวอร์ไม่ถูกต้อง")
    source_file = "hyp_os_game_basic_info.json" if region == "global" else "hyp_cn_game_basic_info.json"
    source_url = f"{CATALOG_BASE.rstrip('/')}/{source_file}"
    validate_public_url(source_url)
    try:
        with urlopen(Request(source_url, headers={"User-Agent": "LOOP Live Wallpaper Hub/0.1"}), timeout=25) as response:
            data = json.loads(response.read(8 * 1024 * 1024))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"โหลดรายการพื้นหลัง HoYoPlay ไม่สำเร็จ: {exc}") from exc
    category = f"{CATALOG_GAMES[game]}_{region}"
    game_names = {"genshin": "Genshin Impact", "zzz": "Zenless Zone Zero", "hsr": "Honkai: Star Rail"}
    entries = []
    for item in data:
        if item.get("type") != "video" or item.get("category") != category:
            continue
        stamp = str(item.get("time", ""))
        date_label = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]}" if len(stamp) >= 12 else "HoYoPlay video"
        url = str(item.get("url", ""))
        meta = item.get("metadata") or {}
        entries.append({"id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:24],
                        "title": f"{game_names[game]} · {date_label}", "url": url,
                        "coverUrl": item.get("cover_url", ""), "category": category, "time": stamp,
                        "width": meta.get("width"), "height": meta.get("height"),
                        "duration": meta.get("duration"), "size": meta.get("size"),
                        "codec": meta.get("codec"), "framerate": meta.get("framerate")})
    return sorted(entries, key=lambda entry: entry["time"], reverse=True)


@app.get("/api/catalog")
async def catalog(game: str = "genshin", region: str = "global") -> dict[str, Any]:
    entries = await asyncio.to_thread(fetch_catalog, game, region)
    return {"game": game, "region": region, "count": len(entries), "items": entries}


@app.post("/api/catalog/import")
async def import_catalog_item(request: CatalogImportRequest) -> dict[str, Any]:
    parsed = urlparse(request.url)
    if parsed.hostname not in CATALOG_ASSET_HOSTS:
        raise HTTPException(status_code=400, detail="URL สื่อไม่อยู่ใน CDN ของคลังเกม")
    body, media_type = await asyncio.to_thread(download_public_asset, request.url, f"{CATALOG_BASE}/")
    suffix = Path(parsed.path).suffix.lower()
    video_suffixes = {".webm", ".mp4", ".m4v", ".mov"}
    if not media_type.startswith("video/") and not (media_type == "application/octet-stream" and suffix in video_suffixes):
        raise HTTPException(status_code=415, detail="รายการนี้ไม่ใช่วิดีโอ")
    extension = suffix if suffix in video_suffixes else ".webm" if media_type == "video/webm" else ".mov" if media_type == "video/quicktime" else ".mp4"
    media_id = uuid.uuid4().hex
    path = MEDIA_DIR / f"{media_id}{extension}"
    path.write_bytes(body)
    duration = await asyncio.to_thread(media_duration, path)
    return {"mediaId": media_id, "mediaUrl": f"/api/media/{media_id}", "fileName": path.name,
            "duration": duration, "kind": "video", "title": request.title, "sourceUrl": request.url}


@app.post("/api/scan")
async def scan(request: ScanRequest) -> dict[str, Any]:
    safe_url = validate_public_url(request.url)
    try:
        final_url, title, candidates = await scan_page(safe_url, request.selector, request.captureSeconds)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"เปิดหรือสแกนหน้าเว็บไม่สำเร็จ: {exc}") from exc
    scan_id = uuid.uuid4().hex
    SCAN_CACHE[scan_id] = {"url": final_url, "title": title, "candidates": {item["id"]: item for item in candidates}}
    while len(SCAN_CACHE) > 64:
        SCAN_CACHE.pop(next(iter(SCAN_CACHE)))
    return {"scanId": scan_id, "url": final_url, "pageTitle": title, "candidates": candidates}


async def record_in_page(page, selector_kind: str, index: int, seconds: int = 12) -> bytes:
    result = await page.evaluate(
        """async ({kind, index, seconds}) => {
          const canvas = kind === 'canvas' ? [...document.querySelectorAll('canvas')][index] : null;
          const video = kind === 'video' ? [...document.querySelectorAll('video')][index] : null;
          if (video) { try { await video.play(); } catch (_) {} }
          if (!canvas && !video) throw new Error('ไม่พบ Canvas/วิดีโอเดิมเมื่อเปิดหน้าเว็บใหม่');
          const width = canvas?.width || video.videoWidth, height = canvas?.height || video.videoHeight;
          if (!width || !height) throw new Error('สื่อยังไม่พร้อมเล่น');
          let stream;
          if (canvas) stream = canvas.captureStream(30);
          else if (video.captureStream) stream = video.captureStream();
          else {
            const target = document.createElement('canvas'); target.width = width; target.height = height;
            const ctx = target.getContext('2d');
            const draw = () => { try { ctx.drawImage(video, 0, 0, width, height); } catch (_) {} requestAnimationFrame(draw); };
            draw(); stream = target.captureStream(30);
          }
          const mime = ['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(t => MediaRecorder.isTypeSupported(t));
          if (!mime) throw new Error('เบราว์เซอร์ไม่รองรับการบันทึก WebM');
          const bitrate = Math.min(50000000, Math.max(12000000, Math.round(width * height * 8)));
          const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: bitrate });
          const chunks = [];
          const finished = new Promise((resolve, reject) => {
            recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
            recorder.onerror = () => reject(recorder.error || new Error('บันทึก Canvas ไม่สำเร็จ'));
            recorder.onstop = () => resolve(new Blob(chunks, {type:mime}));
          });
          recorder.start(); await new Promise(resolve => setTimeout(resolve, seconds * 1000));
          if (recorder.state !== 'inactive') recorder.stop();
          const blob = await finished; const bytes = new Uint8Array(await blob.arrayBuffer());
          let binary = ''; const chunk = 0x8000;
          for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
          return { data: btoa(binary), mime: blob.type, width, height };
        }""",
        {"kind": selector_kind, "index": index, "seconds": seconds},
    )
    import base64
    return base64.b64decode(result["data"])


async def record_element(page, selector: str, seconds: int = 12, fps: int = 10) -> bytes:
    locator = page.locator(selector).first
    if await locator.count() == 0:
        raise ValueError("ไม่พบ CSS selector นี้เมื่อเปิดหน้าเว็บใหม่")
    await locator.wait_for(state="visible", timeout=15000)
    await locator.scroll_into_view_if_needed()
    await page.wait_for_timeout(1200)
    first_png = await locator.screenshot(type="png", animations="allow", timeout=15000)
    first = cv2.imdecode(np.frombuffer(first_png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if first is None:
        raise ValueError("อ่านภาพฉากที่เลือกไม่ได้")
    height, width = first.shape[:2]
    width -= width % 2
    height -= height % 2
    if width < 2 or height < 2:
        raise ValueError("ขนาดฉากที่เลือกเล็กเกินไป")
    fd, temp_name = tempfile.mkstemp(prefix="loop-scene-", suffix=".mp4", dir=MEDIA_DIR)
    os.close(fd)
    output_path = Path(temp_name)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps),
               "-vcodec", "png", "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "ultrafast",
               "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for index in range(seconds * fps):
            png = first_png if index == 0 else await locator.screenshot(type="png", animations="allow", timeout=15000)
            frame = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".png", frame)
            if not ok or process.stdin is None:
                raise RuntimeError("ส่งเฟรมเข้า encoder ไม่สำเร็จ")
            process.stdin.write(encoded.tobytes())
            await asyncio.sleep(1 / fps)
        if process.stdin:
            process.stdin.close()
        error_text = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait(timeout=45)
        if return_code != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(error_text.strip() or "แปลงฉากเป็น MP4 ไม่สำเร็จ")
        return output_path.read_bytes()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        output_path.unlink(missing_ok=True)


def safe_media_path(media_id: str) -> Path:
    if not media_id.isalnum():
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์สื่อ")
    paths = list(MEDIA_DIR.glob(f"{media_id}.*"))
    if not paths:
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์สื่อ")
    return paths[0]


def download_public_asset(url: str, referer: str) -> tuple[bytes, str]:
    class GuardedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_public_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    validate_public_url(url)
    opener = build_opener(GuardedRedirect())
    request = Request(url, headers={"User-Agent": "LOOP Live Wallpaper Hub/0.1", "Referer": referer})
    with opener.open(request, timeout=45) as response:
        final_url = validate_public_url(response.geturl())
        content_type = response.headers.get_content_type()
        if content_type not in {"video/mp4", "video/webm", "video/quicktime", "application/octet-stream", "image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise HTTPException(status_code=415, detail=f"URL นี้ส่งข้อมูลชนิด {content_type} ไม่ใช่ไฟล์ภาพ/วิดีโอ")
        data = response.read(MAX_MEDIA_BYTES + 1)
        if len(data) > MAX_MEDIA_BYTES:
            raise HTTPException(status_code=413, detail="ไฟล์ใหญ่เกินขนาดที่กำหนด")
    return data, content_type


@app.post("/api/capture")
async def capture(request: CaptureRequest) -> dict[str, Any]:
    scan = SCAN_CACHE.get(request.scanId)
    candidate = (scan or {}).get("candidates", {}).get(request.candidateId)
    if not scan or not candidate:
        raise HTTPException(status_code=404, detail="ผลสแกนหมดอายุแล้ว กรุณาสแกนหน้าใหม่")
    media_id = uuid.uuid4().hex
    kind = candidate["kind"]
    if kind == "element":
        url = validate_public_url(scan["url"])
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(service_workers="block", viewport={"width": 1920, "height": 1080})
            await context.route("**/*", browser_route_guard)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(4000)
                body = await record_element(page, candidate["selector"], int(candidate.get("captureSeconds", 12)))
                extension, media_type = ".mp4", "video/mp4"
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"จับฉาก Sprite/DOM ไม่สำเร็จ: {exc}") from exc
            finally:
                await context.close()
                await browser.close()
    elif kind in {"canvas", "video"} and (kind == "canvas" or str(candidate.get("url", "")).startswith("blob:")):
        url = validate_public_url(scan["url"])
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(service_workers="block", viewport={"width": 1920, "height": 1080})
            await context.route("**/*", browser_route_guard)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                index_key = "canvasIndex" if kind == "canvas" else "videoIndex"
                body = await record_in_page(page, kind, int(candidate[index_key]), int(candidate.get("captureSeconds", 12)))
                extension, media_type = ".webm", "video/webm"
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"จับภาพเคลื่อนไหวไม่สำเร็จ: {exc}") from exc
            finally:
                await context.close()
                await browser.close()
    else:
        media_url = candidate.get("url", "")
        if not media_url:
            raise HTTPException(status_code=400, detail="รายการนี้ไม่มี URL ของไฟล์สื่อ")
        body, media_type = await asyncio.to_thread(download_public_asset, media_url, scan["url"])
        extension = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
                     "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(media_type, ".bin")
        if extension == ".bin" and Path(urlparse(media_url).path).suffix.lower() in {".mp4", ".m4v", ".webm", ".mov"}:
            extension = Path(urlparse(media_url).path).suffix.lower()
    path = MEDIA_DIR / f"{media_id}{extension}"
    path.write_bytes(body)
    is_video = extension.lower() in {".mp4", ".m4v", ".webm", ".mov"}
    duration = await asyncio.to_thread(media_duration, path) if is_video else None
    return {"mediaId": media_id, "mediaUrl": f"/api/media/{media_id}", "fileName": path.name, "duration": duration, "kind": kind}


def media_duration(path: Path) -> float:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return 0.0
    count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    return float(count / fps) if fps > 0 and count > 0 else 0.0


@app.post("/api/analyze")
async def analyze(request: MediaRequest) -> dict[str, Any]:
    path = safe_media_path(request.mediaId)
    if path.suffix.lower() not in {".mp4", ".webm", ".mov", ".m4v"}:
        raise HTTPException(status_code=415, detail="วิเคราะห์จุดวนได้เฉพาะไฟล์วิดีโอ")
    try:
        return await asyncio.to_thread(analyze_loop, path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def analyze_loop(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("อ่านวิดีโอไม่ได้")
    fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = frame_count / fps if fps > 0 else 0
    if duration < 0.6:
        capture.release()
        raise ValueError("วิดีโอสั้นเกินไปสำหรับวิเคราะห์ช่วงวน")
    stride = max(1, round(fps / SAMPLE_FPS))
    frames: list[np.ndarray] = []
    frame_index = 0
    while len(frames) < 480:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % stride == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append(cv2.resize(gray, (48, 27), interpolation=cv2.INTER_AREA))
        frame_index += 1
    capture.release()
    if len(frames) < 5:
        raise ValueError("มีเฟรมไม่พอสำหรับเปรียบเทียบ")
    pixels = np.stack(frames).reshape(len(frames), -1).astype(np.int16)
    sample_interval = stride / fps if fps > 0 else 1 / SAMPLE_FPS
    min_gap = max(2, int(round(min(1.0, duration * 0.25) / sample_interval)))
    comparisons: list[tuple[float, int, int]] = []
    for start_index in range(max(0, len(pixels) - min_gap)):
        later = pixels[start_index + min_gap:]
        if not len(later):
            continue
        differences = np.abs(later - pixels[start_index]).mean(axis=1)
        comparisons.extend((float(score), start_index, start_index + min_gap + offset) for offset, score in enumerate(differences))
    if not comparisons:
        raise ValueError("หาเฟรมต้นและท้ายที่เปรียบเทียบได้ไม่พบ")
    best_score = min(item[0] for item in comparisons)
    near_best = [item for item in comparisons if item[0] <= best_score + 1.5]
    score, start_index, end_index = min(near_best, key=lambda item: (item[2] - item[1], item[0]))
    start = start_index * sample_interval
    end = min(duration, end_index * sample_interval)
    similarity = max(0.0, min(100.0, 100.0 * (1 - score / 255)))
    return {"duration": round(duration, 3), "start": round(start, 3), "end": round(end, 3),
            "similarity": round(similarity, 2), "sampleFps": SAMPLE_FPS,
            "note": "เลือกช่วงสั้นที่สุดในกลุ่มเฟรมที่คล้ายกัน จากการเปรียบเทียบภาพย่อ ควรเล่นตัวอย่างและปรับจุดเริ่ม/จบเองก่อนส่งออก"}


@app.get("/api/media/{media_id}")
async def get_media(media_id: str) -> FileResponse:
    path = safe_media_path(media_id)
    return FileResponse(path, media_type={".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                                           ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower()))


@app.post("/api/library")
async def add_to_library(request: LibraryRequest) -> dict[str, Any]:
    safe_media_path(request.mediaId)
    if request.end <= request.start:
        raise HTTPException(status_code=422, detail="จุดจบต้องอยู่หลังจุดเริ่ม")
    item_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO wallpapers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (item_id, request.title, request.sourceUrl, request.mediaId, request.start, request.end, request.similarity, created))
    return {"id": item_id, "title": request.title, "sourceUrl": request.sourceUrl, "mediaId": request.mediaId,
            "mediaUrl": f"/api/media/{request.mediaId}", "start": request.start, "end": request.end,
            "similarity": request.similarity, "createdAt": created}


@app.get("/api/library")
async def get_library() -> list[dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM wallpapers ORDER BY created_at DESC").fetchall()
    return [{"id": row["id"], "title": row["title"], "sourceUrl": row["source_url"], "mediaId": row["media_id"],
             "mediaUrl": f"/api/media/{row['media_id']}", "start": row["start"], "end": row["end"],
             "similarity": row["similarity"], "createdAt": row["created_at"]} for row in rows]


@app.delete("/api/library/{item_id}")
async def delete_from_library(item_id: str) -> dict[str, bool]:
    with sqlite3.connect(DB_PATH) as db:
        cursor = db.execute("DELETE FROM wallpapers WHERE id = ?", (item_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="ไม่พบรายการในคลัง")
    return {"deleted": True}


def build_lively_zip(request: ExportRequest) -> bytes:
    source = safe_media_path(request.mediaId)
    if request.end <= request.start:
        raise HTTPException(status_code=422, detail="จุดจบต้องอยู่หลังจุดเริ่ม")
    title = request.title[:200]
    safe_title = html.escape(title, quote=True)
    file_name = "wallpaper" + source.suffix.lower()
    is_image = source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    escaped_file = file_name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    if is_image:
        media_markup = f'<img alt="" src="{escaped_file}">'
        playback = ""
    else:
        media_markup = f'<video id="wallpaper" autoplay muted playsinline preload="auto" src="{escaped_file}"></video>'
        playback = f"""
const video=document.getElementById('wallpaper');
const start={max(0.0, request.start):.3f}; const end={max(request.start + 0.05, request.end):.3f};
const playFromStart=()=>{{if(video.currentTime<start||video.currentTime>=end)video.currentTime=start;video.play().catch(()=>{{}})}};
video.addEventListener('loadedmetadata',playFromStart);
video.addEventListener('timeupdate',()=>{{if(video.currentTime>=end)video.currentTime=start}});
window.livelyWallpaperPlaybackChanged=(data)=>{{let s=data;try{{if(typeof data==='string')s=JSON.parse(data)}}catch(_){{}}if(s&&s.IsPaused)video.pause();else playFromStart()}};
document.addEventListener('visibilitychange',()=>{{if(document.hidden)video.pause();else playFromStart()}});
"""
    index = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title}</title>
<style>*{{box-sizing:border-box}}html,body{{width:100%;height:100%;margin:0;overflow:hidden;background:#000}}video,img{{position:fixed;inset:0;width:100%;height:100%;object-fit:cover;object-position:center}}</style></head>
<body>{media_markup}<script>{playback}</script></body></html>'''
    info = {"AppVersion": "2.0.6.6", "Title": title, "Thumbnail": None, "Preview": None,
            "Desc": "Loop segment selected by LOOP Live Wallpaper Hub", "Author": "LOOP Live Wallpaper Hub",
            "License": None, "Contact": None, "Type": 1, "FileName": "index.html",
            "Arguments": "--pause-event true", "IsAbsolutePath": False}
    output = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source, file_name)
        archive.writestr("index.html", index)
        archive.writestr("LivelyInfo.json", json.dumps(info, ensure_ascii=False, indent=2))
    output.seek(0)
    return output.read()


@app.post("/api/export")
async def export_project(request: ExportRequest) -> Response:
    archive = await asyncio.to_thread(build_lively_zip, request)
    return Response(content=archive, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="LiveWallpaper-Lively.zip"'})
