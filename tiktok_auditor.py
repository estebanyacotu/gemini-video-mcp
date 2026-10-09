"""Bounded read-only TikTok retrieval for the existing Yaco FastMCP service.

No paid providers are called. Captions use existing Transcriptor, or optional
local faster-whisper (opt in via LOCAL_ASR=1). TikWM is a third-party fallback.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import ipaddress
import json
import math
import os
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit, urlencode, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

import imageio_ffmpeg
from mcp.types import CallToolResult, ImageContent, TextContent

ALLOWED_TIKTOK = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vt.tiktok.com", "vm.tiktok.com"}
TIKWM_URL = "https://www.tikwm.com/api/"
USER_AGENT = "Mozilla/5.0 (compatible; YacoTikTokAuditor/1.1)"
MAX_MB = max(5, min(100, int(os.getenv("TIKTOK_MAX_MB", "60"))))
MAX_BYTES = MAX_MB * 1024 * 1024
CACHE_SECONDS = 600
_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, str, dict]] = {}


class TikTokError(RuntimeError):
    pass


@contextmanager
def media_lock():
    # Hold while reading cached files too: another request must not delete them.
    if not _LOCK.acquire(timeout=3):
        raise TikTokError("Servidor ocupado; reintenta al terminar la petición actual")
    try:
        yield
    finally:
        _LOCK.release()


def uses_cached_media(function):
    @wraps(function)
    def protected(*args, **kwargs):
        with media_lock():
            return function(*args, **kwargs)
    return protected


def normalize_url(value: str) -> str:
    p = urlsplit((value or "").strip())
    if p.scheme != "https" or p.hostname not in ALLOWED_TIKTOK or p.username or p.password or p.port:
        raise ValueError("Proporciona una URL pública HTTPS de TikTok")
    if not p.path or p.path == "/":
        raise ValueError("Falta la ruta del video de TikTok")
    return urlunsplit(("https", p.hostname, p.path, p.query, ""))


def _public_host(hostname: str) -> None:
    # Strict egress guard for URLs supplied by a third-party API.
    if not hostname or hostname.lower() in {"localhost", "metadata.google.internal"}:
        raise TikTokError("Host de medios inválido")
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise TikTokError("No se pudo resolver el host del video") from exc
    for record in records:
        addr = ipaddress.ip_address(record[4][0])
        if not addr.is_global:
            raise TikTokError("No se permiten direcciones internas o privadas")


class SafeRedirects(HTTPRedirectHandler):
    def __init__(self, mode: str):
        self.mode = mode
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urlsplit(newurl)
        if p.scheme != "https" or p.username or p.password or p.port:
            raise TikTokError("Redirección no HTTPS o con credenciales")
        if self.mode == "tiktok":
            if p.hostname not in ALLOWED_TIKTOK:
                raise TikTokError("TikTok redirigió fuera de sus dominios")
        else:
            _public_host(p.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_short_url(url: str) -> str:
    url = normalize_url(url)
    opener = build_opener(SafeRedirects("tiktok"))
    with opener.open(Request(url, headers={"User-Agent": USER_AGENT}), timeout=12) as response:
        return normalize_url(response.url)


def parse_tikwm(payload: bytes) -> tuple[str, dict]:
    if len(payload) > 2_000_000:
        raise TikTokError("Respuesta TikWM demasiado grande")
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise TikTokError("TikWM devolvió JSON inválido") from exc
    if not isinstance(result, dict):
        raise TikTokError("TikWM devolvió una estructura inválida")
    if result.get("code") != 0 or not isinstance(result.get("data"), dict):
        raise TikTokError("TikWM no encontró el video: " + str(result.get("msg", "sin detalles"))[:120])
    data = result["data"]
    link = next((data.get(k) for k in ("hdplay", "play", "wmplay") if isinstance(data.get(k), str) and data.get(k)), None)
    if not link:
        raise TikTokError("TikWM no devolvió enlace reproducible")
    if link.startswith("/") and not link.startswith("//"):
        link = urljoin(TIKWM_URL, link)
    p = urlsplit(link)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port:
        raise TikTokError("Enlace multimedia inválido")
    metadata = {k: data.get(k) for k in ("id", "title", "duration", "create_time")}
    if isinstance(data.get("author"), dict):
        metadata["author"] = {k: data["author"].get(k) for k in ("unique_id", "nickname")}
    return link, metadata


def query_tikwm(url: str) -> tuple[str, dict]:
    body = urlencode({"url": url, "hd": "1"}).encode()
    req = Request(TIKWM_URL, data=body, method="POST", headers={
        "User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"
    })
    with build_opener(SafeRedirects("media")).open(req, timeout=20) as response:
        return parse_tikwm(response.read(2_000_001))


def download_media(url: str, destination: Path) -> int:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port:
        raise TikTokError("Enlace de video inválido")
    _public_host(p.hostname)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.tiktok.com/"})
    deadline = time.monotonic() + 60
    try:
        with build_opener(SafeRedirects("media")).open(req, timeout=25) as response, destination.open("wb") as out:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_BYTES:
                raise TikTokError("Video mayor al tamaño permitido")
            header = response.read(64)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise TikTokError("Se recibió HTML u otro contenido; falta MP4")
            out.write(header)
            size = len(header)
            while block := response.read(128 * 1024):
                if time.monotonic() > deadline:
                    raise TikTokError("La descarga excedió el tiempo permitido")
                size += len(block)
                if size > MAX_BYTES:
                    raise TikTokError("Video mayor al límite")
                out.write(block)
        return size
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        # Render's Python runtime may only have imageio's bundled ffmpeg.
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, timeout=15)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if not match or "Video:" not in r.stderr:
            raise TikTokError("No se pudo comprobar duración y pista de video")
        hours, minutes, seconds = map(float, match.groups())
        duration = hours * 3600 + minutes * 60 + seconds
        if not math.isfinite(duration) or duration <= 0:
            raise TikTokError("Duración del video inválida")
        dimensions = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", r.stderr)
        streams = [{"type": "video"}]
        if dimensions:
            streams[0].update(width=int(dimensions[1]), height=int(dimensions[2]))
        if "Audio:" in r.stderr:
            streams.append({"type": "audio"})
        return {"duration_seconds": duration, "streams": streams, "probe": "ffmpeg"}
    r = subprocess.run([exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=15)
    if r.returncode:
        raise TikTokError("El archivo MP4 no pudo inspeccionarse con ffprobe")
    data = json.loads(r.stdout)
    duration = float(data.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0 or not any(
        s.get("codec_type") == "video" for s in data.get("streams", [])
    ):
        raise TikTokError("El archivo no contiene video con duración válida")
    return {"duration_seconds": duration, "probe": "ffprobe",
            "streams": [{"type": s.get("codec_type"), "codec": s.get("codec_name"),
                         "width": s.get("width"), "height": s.get("height")}
                        for s in data.get("streams", [])]}


def _yt_download(url: str, path: Path) -> dict:
    args = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-progress", "--no-warnings",
            "--max-filesize", f"{MAX_MB}M", "--socket-timeout", "12", "--retries", "1",
            "--format", "best[ext=mp4]/best", "-o", str(path), url]
    r = subprocess.run(args, capture_output=True, text=True, timeout=65)
    if r.returncode or not path.is_file() or path.stat().st_size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise TikTokError("yt-dlp no pudo recuperar el MP4")
    with path.open("rb") as f:
        if f.read(12)[4:8] != b"ftyp":
            path.unlink(missing_ok=True)
            raise TikTokError("yt-dlp no produjo un MP4 válido")
    return {"provider": "yt-dlp"}


def obtain_tiktok(url: str) -> tuple[Path, dict]:
    original = normalize_url(url)
    key = hashlib.sha256(original.encode()).hexdigest()
    with media_lock():
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] <= CACHE_SECONDS and Path(cached[1]).is_file():
            return Path(cached[1]), cached[2]
        work = Path(tempfile.gettempdir()) / "yaco-tiktok" / key
        # Clear stale/failed downloads, including .part files, before retrieving.
        root = work.parent
        if root.exists():
            for stale in root.iterdir():
                if stale.is_dir() and re.fullmatch(r"[0-9a-f]{64}", stale.name):
                    shutil.rmtree(stale, ignore_errors=True)
        _CACHE.clear()
        work.mkdir(parents=True, exist_ok=True)
        path = work / "clip.mp4"
        path.unlink(missing_ok=True)
        try:
            resolved = resolve_short_url(original)
            redirect_error = None
        except (URLError, OSError, ValueError, TikTokError) as exc:
            resolved, redirect_error = original, str(exc)[:180]
        try:
            source_info = _yt_download(resolved, path)
        except (OSError, subprocess.TimeoutExpired, TikTokError):
            link, metadata = query_tikwm(resolved)
            download_media(link, path)
            source_info = {"provider": "TikWM", "metadata": metadata}
        technical = _probe(path)
        result = {"requested_url": original, "resolved_url": resolved if not redirect_error else None,
                  "resolution_error": redirect_error, "retrieval": source_info,
                  "bytes": path.stat().st_size, "technical": technical,
                  "audio_transcript_status": "pendiente_de_comprobar", "visual_coverage": "no_inspeccionado"}
        # Bound disk use to a single recent item per worker.
        for old_key, entry in list(_CACHE.items()):
            if old_key != key:
                shutil.rmtree(Path(entry[1]).parent, ignore_errors=True)
                _CACHE.pop(old_key, None)
        _CACHE[key] = (time.monotonic(), str(path), result)
        return path, result


@uses_cached_media
def capture_tiktok_frame(url: str, seconds: float = 0.0, width: int = 720) -> dict:
    seconds = float(seconds)
    if not (0 <= seconds <= 600):
        raise ValueError("El segundo debe estar entre 0 y 600")
    width = max(240, min(1080, int(width)))
    path, info = obtain_tiktok(url)
    duration = info["technical"].get("duration_seconds")
    if duration and seconds >= duration:
        raise ValueError("El tiempo excede la duración comprobada")
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(seconds),
           "-i", str(path), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4",
           "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    if r.returncode or not r.stdout.startswith(b"\xff\xd8") or len(r.stdout) > 2_000_000:
        raise TikTokError("No se obtuvo el fotograma solicitado")
    return {"requested_url": info["requested_url"], "seconds": seconds,
            "mimeType": "image/jpeg", "image_base64": base64.b64encode(r.stdout).decode("ascii"),
            "sampling": "fotograma_individual; no_equivale_a_reproduccion_continua"}


@uses_cached_media
def captions_tiktok(url: str) -> dict:
    """Use the existing source-caption extractor; optional CPU ASR needs explicit setup."""
    from transcriptor import _subtitle_payload
    _, metadata = obtain_tiktok(url)
    resolved = metadata["resolved_url"] or metadata["requested_url"]
    try:
        result = _subtitle_payload(resolved, raw=True, format="srt")
        return {**result, "evidence_type": "subtitulos_proveedor", "literal_verified_by_listening": False}
    except Exception as exc:
        failure = str(exc)[:250]
    if os.getenv("LOCAL_ASR") != "1":
        return {"status": "transcripcion_no_obtenida", "details": failure,
                "local_asr": "no habilitado", "literal_verified_by_listening": False}
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"status": "asr_no_disponible", "details": "Instala faster-whisper en el servidor"}
    path, metadata = obtain_tiktok(url)
    duration = metadata["technical"].get("duration_seconds")
    if duration is None or duration > 180:
        return {"status": "asr_no_ejecutado", "details": "Audio > 180s o duración desconocida"}
    model_name = os.getenv("LOCAL_ASR_MODEL", "tiny")
    with tempfile.TemporaryDirectory(prefix="yaco-audio-") as tmp:
        wav = str(Path(tmp) / "audio.wav")
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
               "-vn", "-ac", "1", "-ar", "16000", "-t", "180", "-y", wav]
        subprocess.run(cmd, check=True, timeout=30, capture_output=True)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(wav, language="es", vad_filter=True)
        records = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    return {"status": "transcrito", "evidence_type": "asr_automatico_local",
            "literal_verified_by_listening": False, "model": model_name, "segments": records}


def register_tiktok_tools(mcp):
    annotations = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

    @mcp.tool(annotations=annotations)
    async def obtener_tiktok(url: str) -> dict:
        """Resolve a public TikTok short/full URL, retrieve a bounded MP4 at no vidIQ cost, return measured metadata and availability. Use before visual/audio review."""
        _path, result = await asyncio.to_thread(obtain_tiktok, url)
        return result

    @mcp.tool(annotations=annotations)
    async def fotograma_tiktok(url: str, seconds: float = 0, width: int = 720) -> CallToolResult:
        """Return a native MCP image plus source and clip-relative timestamp. One sampled frame is not continuous playback."""
        frame = await asyncio.to_thread(capture_tiktok_frame, url, seconds, width)
        data = frame.pop("image_base64")
        return CallToolResult(content=[
            TextContent(type="text", text=json.dumps(frame, ensure_ascii=False)),
            ImageContent(type="image", data=data, mimeType=frame["mimeType"]),
        ])

    @mcp.tool(annotations=annotations)
    async def transcripcion_tiktok(url: str) -> dict:
        """Return timestamped original provider captions or optional on-server CPU ASR; label source and never invent verbatim speech."""
        return await asyncio.to_thread(captions_tiktok, url)

