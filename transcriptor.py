import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

import imageio_ffmpeg


YT_DLP_TIMEOUT = int(os.environ.get("YT_DLP_TIMEOUT", "60"))
MAX_CONCURRENCY = max(1, int(os.environ.get("YT_DLP_MAX_CONCURRENCY", "3")))
MAX_QUEUE = max(0, int(os.environ.get("YT_DLP_MAX_QUEUE", "6")))
MAX_PLAYLIST_ITEMS = max(1, int(os.environ.get("YT_DLP_MAX_PLAYLIST_ITEMS", "25")))
MAX_SEARCH_RESULTS = max(1, int(os.environ.get("YT_DLP_MAX_SEARCH_RESULTS", "50")))
METADATA_CACHE_TTL = max(0, int(os.environ.get("YT_DLP_METADATA_CACHE_TTL", "90")))
TRANSCRIPT_CACHE_TTL = max(0, int(os.environ.get("YT_DLP_TRANSCRIPT_CACHE_TTL", "900")))
SUBTITLE_HOLD_BASE = max(30, int(os.environ.get("SUBTITLES_RATE_LIMIT_HOLD_SECONDS", "600")))
SUBTITLE_HOLD_MAX = max(SUBTITLE_HOLD_BASE, int(os.environ.get("SUBTITLES_RATE_LIMIT_HOLD_MAX_SECONDS", "3600")))
YT_DLP_JS_RUNTIMES = os.environ.get("YT_DLP_JS_RUNTIMES", "deno").strip()
YT_DLP_REMOTE_COMPONENTS = os.environ.get("YT_DLP_REMOTE_COMPONENTS", "ejs:github").strip()
YT_DLP_EXTRACTOR_ARGS = os.environ.get(
    "YT_DLP_EXTRACTOR_ARGS",
    "youtube:player_client=mweb;fetch_pot=always",
).strip()
YT_DLP_DIAGNOSTICS = os.environ.get("YT_DLP_DIAGNOSTICS", "0").strip().lower() in {"1", "true", "yes"}
BGUTIL_ENABLED = os.environ.get("BGUTIL_POT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
BGUTIL_VERSION = os.environ.get("BGUTIL_POT_VERSION", "2.0.1").strip()
BGUTIL_BOOTSTRAP_TIMEOUT = max(30, int(os.environ.get("BGUTIL_BOOTSTRAP_TIMEOUT", "180")))
BGUTIL_ARCHIVE_URL = os.environ.get(
    "BGUTIL_POT_ARCHIVE_URL",
    f"https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/{BGUTIL_VERSION}.zip",
).strip()

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
SEARCH_ANNOTATIONS = {
    **READ_ONLY_ANNOTATIONS,
    "idempotentHint": False,
}

_ALLOWED_HOSTS = (
    "youtube.com", "youtu.be", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "twitch.tv", "vimeo.com", "facebook.com", "fb.watch",
    "bilibili.com", "vk.com", "dailymotion.com", "dai.ly", "reddit.com",
)

_pool = threading.BoundedSemaphore(MAX_CONCURRENCY)
_queue_lock = threading.Lock()
_waiting = 0

_cache_lock = threading.Lock()
_info_cache: dict[str, tuple[float, dict]] = {}
_transcript_cache: dict[str, tuple[float, str]] = {}

_caption_lock = threading.Lock()
_caption_hold_until = 0.0
_caption_next_hold = SUBTITLE_HOLD_BASE

_provider_lock = threading.Lock()
_provider_home: str | None = None


def _cache_get(cache: dict, key: str, ttl: int):
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _cache_lock:
        item = cache.get(key)
        if not item:
            return None
        created, value = item
        if now - created > ttl:
            cache.pop(key, None)
            return None
        return value


def _cache_put(cache: dict, key: str, value, max_items: int):
    if max_items <= 0:
        return
    with _cache_lock:
        cache[key] = (time.monotonic(), value)
        while len(cache) > max_items:
            oldest = min(cache.items(), key=lambda kv: kv[1][0])[0]
            cache.pop(oldest, None)


def _check_caption_hold():
    with _caption_lock:
        remaining = _caption_hold_until - time.monotonic()
    if remaining > 0:
        raise RuntimeError(
            f"rate_limited: subtitle provider is temporarily paused; retry in {int(remaining) + 1}s"
        )


def _mark_caption_rate_limited():
    global _caption_hold_until, _caption_next_hold
    with _caption_lock:
        hold = _caption_next_hold
        _caption_hold_until = max(_caption_hold_until, time.monotonic() + hold)
        _caption_next_hold = min(SUBTITLE_HOLD_MAX, max(SUBTITLE_HOLD_BASE, hold * 2))


def _clear_caption_hold():
    global _caption_hold_until, _caption_next_hold
    with _caption_lock:
        _caption_hold_until = 0.0
        _caption_next_hold = SUBTITLE_HOLD_BASE



def _is_youtube_invocation(args: tuple[str, ...]) -> bool:
    return any(
        "youtube.com/" in value
        or "youtu.be/" in value
        or value.startswith("ytsearch")
        for value in args
    )


def _safe_extract_bgutil_server(archive_bytes: bytes, target: Path) -> None:
    prefix = f"bgutil-ytdlp-pot-provider-{BGUTIL_VERSION}/server/"
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        found = False
        for member in archive.infolist():
            if not member.filename.startswith(prefix):
                continue
            relative = member.filename[len(prefix):]
            if not relative:
                continue
            destination = (target / relative).resolve()
            if target_root not in destination.parents and destination != target_root:
                raise RuntimeError("unsafe path in bgutil provider archive")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(destination, "wb") as output:
                shutil.copyfileobj(source, output)
            found = True
        if not found:
            raise RuntimeError("bgutil provider archive did not contain server files")


def _ensure_bgutil_provider() -> str | None:
    global _provider_home
    if not BGUTIL_ENABLED:
        return None
    if _provider_home and Path(_provider_home, "src", "generate_once.ts").is_file():
        return _provider_home

    with _provider_lock:
        if _provider_home and Path(_provider_home, "src", "generate_once.ts").is_file():
            return _provider_home

        base = Path(tempfile.gettempdir()) / f"yaco-bgutil-{BGUTIL_VERSION}"
        server_home = base / "server"
        script = server_home / "src" / "generate_once.ts"
        ready = server_home / ".yaco-ready"

        if not script.is_file():
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
            request = Request(
                BGUTIL_ARCHIVE_URL,
                headers={"User-Agent": "Yaco-Transcriptor/1.0"},
            )
            try:
                with urlopen(request, timeout=60) as response:
                    archive_bytes = response.read(20 * 1024 * 1024 + 1)
            except Exception as exc:
                raise RuntimeError(
                    f"failed to download bgutil provider {BGUTIL_VERSION}: {exc}"
                ) from exc
            if len(archive_bytes) > 20 * 1024 * 1024:
                raise RuntimeError("bgutil provider archive exceeded 20 MB")
            _safe_extract_bgutil_server(archive_bytes, server_home)

        if not ready.is_file():
            deno = shutil.which("deno")
            if not deno:
                raise RuntimeError("Deno runtime is unavailable for bgutil PO-token provider")
            try:
                install = subprocess.run(
                    [deno, "install", "--allow-scripts=npm:canvas", "--frozen"],
                    cwd=str(server_home),
                    capture_output=True,
                    text=True,
                    timeout=BGUTIL_BOOTSTRAP_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("bgutil provider dependency install timed out") from exc
            if install.returncode != 0:
                detail = (install.stderr or install.stdout or "deno install failed")[-3000:]
                raise RuntimeError(f"bgutil provider setup failed: {detail}")
            ready.write_text(BGUTIL_VERSION, encoding="utf-8")

        _provider_home = str(server_home)
        return _provider_home

def _normalize_url(raw: str) -> str:
    if not raw:
        raise ValueError("url is required")
    value = raw.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return f"https://www.youtube.com/watch?v={value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https video URLs are supported")
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS):
        raise ValueError(f"Unsupported video host: {host}")
    return value


def _run(args: list[str], timeout: int | None = None, binary: bool = False):
    global _waiting
    acquired = _pool.acquire(blocking=False)
    if not acquired:
        with _queue_lock:
            if _waiting >= MAX_QUEUE:
                raise RuntimeError("server busy: yt-dlp queue is full")
            _waiting += 1
        try:
            acquired = _pool.acquire(timeout=max(1, (timeout or YT_DLP_TIMEOUT) * 2))
            if not acquired:
                raise RuntimeError("server busy: timed out waiting for a worker")
        finally:
            with _queue_lock:
                _waiting -= 1

    try:
        attempts = 3
        last_error = None
        for attempt in range(attempts):
            try:
                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=not binary,
                    timeout=timeout or YT_DLP_TIMEOUT,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                last_error = RuntimeError(
                    f"command timed out after {timeout or YT_DLP_TIMEOUT}s"
                )
                if attempt + 1 < attempts:
                    time.sleep(1 + attempt * 2)
                    continue
                raise last_error from exc

            if result.returncode == 0:
                if YT_DLP_DIAGNOSTICS and result.stderr:
                    diagnostic = result.stderr.decode("utf-8", "replace") if binary else result.stderr
                    print(diagnostic[-12000:], file=sys.stderr, flush=True)
                return result.stdout

            stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
            message = (stderr or f"command failed with {result.returncode}")[-3000:]
            last_error = RuntimeError(message)
            transient = any(
                marker in message.lower()
                for marker in (
                    "429",
                    "too many requests",
                    "temporarily unavailable",
                    "http error 500",
                    "http error 502",
                    "http error 503",
                    "http error 504",
                    "remote end closed connection",
                    "connection reset",
                    "timed out",
                )
            )
            if transient and attempt + 1 < attempts:
                time.sleep(1 + attempt * 2)
                continue
            raise last_error

        raise last_error or RuntimeError("command failed")
    finally:
        _pool.release()


def _ytdlp(*args: str, timeout: int | None = None, binary: bool = False):
    common = ["--no-progress", "-R", "3", "--retry-sleep", "linear=1::2"]
    if YT_DLP_DIAGNOSTICS:
        common.append("-v")
    if YT_DLP_JS_RUNTIMES:
        common.extend(["--js-runtimes", YT_DLP_JS_RUNTIMES])
    if YT_DLP_REMOTE_COMPONENTS:
        common.extend(["--remote-components", YT_DLP_REMOTE_COMPONENTS])
    if YT_DLP_EXTRACTOR_ARGS:
        common.extend(["--extractor-args", YT_DLP_EXTRACTOR_ARGS])
    if BGUTIL_ENABLED and _is_youtube_invocation(args):
        provider_home = _ensure_bgutil_provider()
        if provider_home:
            common.extend([
                "--extractor-args",
                f"youtubepot-bgutilscript:server_home={provider_home}",
            ])
    return _run(
        [sys.executable, "-m", "yt_dlp", *common, *args],
        timeout=timeout,
        binary=binary,
    )


def _info(url: str) -> dict:
    normalized = _normalize_url(url)
    cached = _cache_get(_info_cache, normalized, METADATA_CACHE_TTL)
    if cached is not None:
        return cached
    out = _ytdlp(
        "--no-playlist", "--skip-download",
        "--ignore-no-formats-error", "-J", normalized
    )
    info = json.loads(out)
    _cache_put(_info_cache, normalized, info, 4)
    return info


def _num(value):
    return value if isinstance(value, (int, float)) else None


def _str(value):
    return value if isinstance(value, str) else None


def _choose_track(info: dict, track_type: str | None, lang: str | None, fmt: str):
    official = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    original = info.get("language") or info.get("original_language")

    chosen_lang = lang or original
    chosen_type = track_type

    def present(bucket, key):
        return bool(key and isinstance(bucket.get(key), list) and bucket[key])

    if chosen_lang and not chosen_type:
        if present(official, chosen_lang):
            chosen_type = "official"
        elif present(auto, chosen_lang):
            chosen_type = "auto"

    if not chosen_lang:
        keys = list(official) + [k for k in auto if k not in official]
        if len(keys) == 1:
            chosen_lang = keys[0]
        elif "en" in keys:
            chosen_lang = "en"
        elif keys:
            chosen_lang = keys[0]

    if not chosen_type:
        if present(official, chosen_lang):
            chosen_type = "official"
        elif present(auto, chosen_lang):
            chosen_type = "auto"

    bucket = official if chosen_type == "official" else auto
    tracks = bucket.get(chosen_lang or "") or []
    if not tracks:
        raise ValueError(
            "Subtitle track unavailable. "
            f"official={list(official)[:20]} auto={list(auto)[:20]}"
        )

    preferred = next((t for t in tracks if t.get("ext") == fmt), None)
    preferred = preferred or next((t for t in tracks if t.get("ext") == "vtt"), None)
    preferred = preferred or next((t for t in tracks if t.get("ext") == "srt"), None)
    preferred = preferred or tracks[0]
    return chosen_type or "auto", chosen_lang or "und", preferred


def _download_subtitle(
    url: str,
    track_type: str,
    lang: str,
    fmt: str,
) -> str:
    _check_caption_hold()
    normalized = _normalize_url(url)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="yaco-subs-") as temp_dir:
        output = str(Path(temp_dir) / "subtitle.%(ext)s")
        args = [
            "--no-playlist",
            "--skip-download",
            "--write-subs" if track_type == "official" else "--write-auto-subs",
            "--sub-langs", lang,
            "--sub-format", "best",
            "--convert-subs", fmt,
            "--ffmpeg-location", ffmpeg,
            "--output", output,
            normalized,
        ]
        try:
            _ytdlp(*args, timeout=max(YT_DLP_TIMEOUT, 120))
        except Exception as exc:
            message = str(exc).lower()
            if "429" in message or "too many requests" in message:
                _mark_caption_rate_limited()
                raise RuntimeError(
                    "rate_limited: subtitle provider returned HTTP 429; retry later"
                ) from exc
            raise

        candidates = sorted(
            p for p in Path(temp_dir).iterdir()
            if p.is_file() and p.suffix.lower().lstrip(".") == fmt
        )
        if not candidates:
            candidates = sorted(p for p in Path(temp_dir).iterdir() if p.is_file())
        if not candidates:
            raise RuntimeError(
                f"subtitle download produced no file for type={track_type} lang={lang}"
            )
        body = candidates[0].read_text(encoding="utf-8", errors="replace")
        if not body.strip():
            raise RuntimeError("subtitle download returned empty content")
        _clear_caption_hold()
        return body


def _clean_subtitles(raw: str) -> str:
    lines = []
    for line in raw.replace("\ufeff", "").splitlines():
        s = line.strip()
        if not s or s == "WEBVTT" or s.isdigit():
            continue
        if "-->" in s:
            continue
        if s.startswith(("NOTE", "STYLE", "REGION")):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"\{\\[^}]+\}", "", s)
        s = html.unescape(s).strip()
        if s and (not lines or lines[-1] != s):
            lines.append(s)
    return "\n".join(lines)


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid next_cursor") from exc
    if value < 0:
        raise ValueError("Invalid next_cursor")
    return value


def _page(
    text: str,
    response_limit: int | None,
    next_cursor: str | None,
    _parts: dict | None = None,
):
    start = _decode_cursor(next_cursor)
    if start > len(text):
        raise ValueError("next_cursor is beyond content length")
    if response_limit is None:
        limit = len(text) - start
    else:
        limit = int(response_limit)
        if limit < 1000:
            raise ValueError("response_limit must be at least 1000")
    end = min(len(text), start + limit)
    cursor = str(end) if end < len(text) else None
    return text[start:end], start, end, cursor


def _subtitle_payload(
    url: str,
    raw: bool = False,
    type: str | None = None,
    lang: str | None = None,
    format: str = "srt",
    response_limit: int | None = None,
    next_cursor: str | None = None,
):
    if format not in {"srt", "vtt", "ass", "lrc"}:
        raise ValueError("format must be srt, vtt, ass, or lrc")
    if type not in {None, "official", "auto"}:
        raise ValueError("type must be official or auto")
    normalized = _normalize_url(url)
    info = _info(normalized)
    chosen_type, chosen_lang, _track = _choose_track(info, type, lang, format)
    cache_key = json.dumps(
        {
            "url": normalized,
            "raw": raw,
            "type": chosen_type,
            "lang": chosen_lang,
            "format": format,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    text = _cache_get(_transcript_cache, cache_key, TRANSCRIPT_CACHE_TTL)
    if text is None:
        body = _download_subtitle(normalized, chosen_type, chosen_lang, format)
        text = body if raw else _clean_subtitles(body)
        _cache_put(_transcript_cache, cache_key, text, 4)
    parts = {
        "url": normalized, "raw": raw, "type": chosen_type,
        "lang": chosen_lang, "format": format,
    }
    chunk, start, end, cursor = _page(text, response_limit, next_cursor, parts)
    base = {
        "videoId": str(info.get("id") or ""),
        "type": chosen_type,
        "lang": chosen_lang,
        "next_cursor": cursor,
        "is_truncated": cursor is not None,
        "total_length": len(text),
        "start_offset": start,
        "end_offset": end,
        "source": "yt-dlp",
    }
    if raw:
        return {**base, "format": format, "content": chunk}
    return {**base, "url": info.get("webpage_url") or normalized, "text": chunk}


def register_transcriptor_tools(mcp):
    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_transcript(
        url: str,
        type: str | None = None,
        lang: str | None = None,
        format: str = "srt",
        response_limit: int | None = None,
        next_cursor: str | None = None,
    ) -> dict:
        """Fetch cleaned subtitles as plain text for a supported video."""
        return _subtitle_payload(
            url, False, type, lang, format, response_limit, next_cursor
        )

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_raw_subtitles(
        url: str,
        type: str | None = None,
        lang: str | None = None,
        format: str = "srt",
        response_limit: int | None = None,
        next_cursor: str | None = None,
    ) -> dict:
        """Fetch raw subtitles including timestamps/markup."""
        return _subtitle_payload(
            url, True, type, lang, format, response_limit, next_cursor
        )

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_available_subtitles(url: str) -> dict:
        """List official and auto-generated subtitle tracks."""
        info = _info(url)
        return {
            "videoId": str(info.get("id") or ""),
            "official": sorted((info.get("subtitles") or {}).keys()),
            "auto": sorted((info.get("automatic_captions") or {}).keys()),
        }

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_video_info(url: str) -> dict:
        """Fetch extended video metadata."""
        info = _info(url)
        return {
            "videoId": str(info.get("id") or ""),
            "title": _str(info.get("title")),
            "uploader": _str(info.get("uploader")),
            "uploaderId": _str(info.get("uploader_id")),
            "channel": _str(info.get("channel")),
            "channelId": _str(info.get("channel_id")),
            "channelUrl": _str(info.get("channel_url")),
            "duration": _num(info.get("duration")),
            "description": _str(info.get("description")),
            "uploadDate": _str(info.get("upload_date")),
            "webpageUrl": _str(info.get("webpage_url")),
            "viewCount": _num(info.get("view_count")),
            "likeCount": _num(info.get("like_count")),
            "commentCount": _num(info.get("comment_count")),
            "tags": info.get("tags") if isinstance(info.get("tags"), list) else None,
            "categories": info.get("categories") if isinstance(info.get("categories"), list) else None,
            "liveStatus": _str(info.get("live_status")),
            "isLive": info.get("is_live") if isinstance(info.get("is_live"), bool) else None,
            "wasLive": info.get("was_live") if isinstance(info.get("was_live"), bool) else None,
            "availability": _str(info.get("availability")),
            "thumbnail": _str(info.get("thumbnail")),
            "thumbnails": info.get("thumbnails") if isinstance(info.get("thumbnails"), list) else None,
        }

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_video_chapters(url: str) -> dict:
        """Fetch chapter markers for a video."""
        info = _info(url)
        chapters = []
        for ch in info.get("chapters") or []:
            chapters.append({
                "startTime": float(ch.get("start_time") or 0),
                "endTime": float(ch.get("end_time") or ch.get("start_time") or 0),
                "title": str(ch.get("title") or ""),
            })
        return {"videoId": str(info.get("id") or ""), "chapters": chapters}

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_video_frame(
        url: str,
        seconds: float = 0,
        timecode: str | None = None,
        format: str = "jpeg",
        width: int = 1280,
        quality: int = 4,
    ) -> dict:
        """Capture one frame and return it as base64 plus metadata."""
        if timecode:
            parts = [float(p) for p in timecode.split(":")]
            if len(parts) == 2:
                seconds = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                raise ValueError("Invalid timecode")
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        if format not in {"jpeg", "png"}:
            raise ValueError("format must be jpeg or png")
        width = min(1920, max(64, int(width)))
        quality = min(31, max(2, int(quality)))
        normalized = _normalize_url(url)
        info = _info(normalized)
        media = _ytdlp(
            "--no-playlist", "-f",
            "bestvideo[height<=1080]/best[height<=1080]/best",
            "-g", normalized, timeout=90
        ).splitlines()[0]
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        codec = "mjpeg" if format == "jpeg" else "png"
        ff_args = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", str(seconds), "-i", media, "-frames:v", "1",
            "-vf", f"scale='min({width},iw)':-2",
        ]
        if format == "jpeg":
            ff_args += ["-q:v", str(quality)]
        ff_args += ["-f", "image2pipe", "-vcodec", codec, "pipe:1"]
        image = _run(ff_args, timeout=120, binary=True)
        return {
            "videoId": str(info.get("id") or ""),
            "url": info.get("webpage_url") or normalized,
            "timestampSeconds": seconds,
            "mimeType": "image/jpeg" if format == "jpeg" else "image/png",
            "sizeBytes": len(image),
            "width": width,
            "image_base64": base64.b64encode(image).decode(),
        }

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_playlist_transcripts(
        url: str,
        lang: str,
        type: str = "auto",
        format: str = "srt",
        playlistItems: str | None = None,
        maxItems: int = 10,
    ) -> dict:
        """Fetch cleaned transcripts for a bounded set of playlist videos."""
        maxItems = min(MAX_PLAYLIST_ITEMS, max(1, int(maxItems)))
        args = [
            "--flat-playlist", "--dump-json", "--no-progress",
            "--max-downloads", str(maxItems),
        ]
        if playlistItems:
            args += ["--playlist-items", playlistItems]
        args.append(_normalize_url(url))
        lines = _ytdlp(*args, timeout=120).splitlines()
        results = []
        for line in lines[:maxItems]:
            item = json.loads(line)
            item_url = item.get("webpage_url")
            if not item_url and item.get("id"):
                item_url = f"https://www.youtube.com/watch?v={item['id']}"
            if not item_url:
                continue
            try:
                payload = _subtitle_payload(
                    item_url, False, type, lang, format, None, None
                )
                results.append({"videoId": payload["videoId"], "text": payload["text"]})
            except Exception as exc:
                results.append({
                    "videoId": str(item.get("id") or ""),
                    "text": f"[transcript unavailable: {str(exc)[:240]}]",
                })
        return {"results": results}

    @mcp.tool(annotations=SEARCH_ANNOTATIONS)
    def search_videos(
        query: str,
        limit: int = 10,
        offset: int = 0,
        uploadDateFilter: str | None = None,
        dateBefore: str | None = None,
        date: str | None = None,
        matchFilter: str | None = None,
        response_format: str = "json",
    ) -> dict:
        """Search YouTube through yt-dlp with pagination and filters."""
        if not query or not query.strip():
            raise ValueError("query is required")
        limit = min(MAX_SEARCH_RESULTS, max(1, int(limit)))
        offset = max(0, int(offset))
        requested = min(MAX_SEARCH_RESULTS, limit + offset)
        args = ["--flat-playlist", "--dump-json", "--skip-download", "--no-progress"]
        date_map = {
            "hour": "now-1hour", "today": "today",
            "week": "now-1week", "month": "now-1month", "year": "now-1year",
        }
        if uploadDateFilter:
            args += ["--dateafter", date_map.get(uploadDateFilter, uploadDateFilter)]
        if dateBefore:
            args += ["--datebefore", dateBefore]
        if date:
            args += ["--date", date]
        if matchFilter:
            args += ["--match-filter", matchFilter]
        args.append(f"ytsearch{requested}:{query.strip()}")
        rows = [json.loads(x) for x in _ytdlp(*args, timeout=120).splitlines() if x.strip()]
        rows = rows[offset:offset + limit]
        return {
            "results": [{
                "videoId": str(x.get("id") or ""),
                "title": _str(x.get("title")),
                "url": _str(x.get("webpage_url") or x.get("url")),
                "duration": _num(x.get("duration")),
                "uploader": _str(x.get("uploader") or x.get("channel")),
                "viewCount": _num(x.get("view_count")),
                "thumbnail": _str(x.get("thumbnail")),
            } for x in rows]
        }
