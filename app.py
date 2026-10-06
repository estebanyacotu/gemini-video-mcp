import os
import re
import shutil
import uvicorn
from starlette.responses import JSONResponse
import tempfile
import time
import urllib.request
from typing_extensions import TypedDict, Required, NotRequired
from urllib.parse import urlparse, parse_qs

from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP

from transcriptor import register_transcriptor_tools


PORT = int(os.environ.get("PORT", 8000))
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

mcp = FastMCP(
    "Yaco Video MCP",
    port=PORT,
    host="0.0.0.0",
    stateless_http=True,
    json_response=True,
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(
        attempts=3, initial_delay=1, max_delay=5,
        http_status_codes=[429, 500, 502, 503, 504],
    )),
) if os.environ.get("GEMINI_API_KEY") else None


def generate_video(contents):
    if client is None:
        raise RuntimeError("Falta GEMINI_API_KEY en Render; la extracción de subtítulos sigue disponible.")
    response = client.models.generate_content(model=MODEL, contents=contents)
    if not response.text:
        raise RuntimeError("Gemini respondió sin texto.")
    return response.text


def download_openai_file(url, local_path):
    if urlparse(url).scheme != "https":
        raise ValueError("El archivo debe tener una URL HTTPS autorizada.")
    request = urllib.request.Request(url, headers={"User-Agent": "Yaco-Transcriptor/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, open(local_path, "wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)



class OpenAIFile(TypedDict):
    download_url: Required[str]
    file_id: Required[str]
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


def normalize_youtube_url(raw_url: str) -> str:
    if not raw_url:
        raise ValueError("La URL está vacía.")
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("La URL debe usar http o https.")
    host = (parsed.hostname or "").lower()
    video_id = None
    if host == "youtu.be":
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            video_id = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
    else:
        raise ValueError("La URL no pertenece a YouTube.")
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("No se pudo obtener un ID válido de YouTube.")
    return f"https://www.youtube.com/watch?v={video_id}"


def wait_until_active(gemini_file, timeout_seconds: int = 120):
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = getattr(getattr(gemini_file, "state", None), "name", None)
        if state == "ACTIVE":
            return gemini_file
        if state in {"FAILED", "ERROR"}:
            raise RuntimeError("Gemini no pudo procesar el video.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Gemini tardó demasiado en procesar el video.")
        time.sleep(2)
        gemini_file = client.files.get(name=gemini_file.name)


@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: OpenAIFile | None = None,
    url: str | None = None,
    instruccion: str = (
        "Analiza detalladamente este video. "
        "Identifica los puntos clave, marcas de tiempo y mejores momentos."
    ),
) -> str:
    if video is not None and url:
        raise ValueError("Envía un MP4 o una URL de YouTube, no ambos.")

    if url:
        youtube_url = normalize_youtube_url(url)
        return generate_video([
            types.Part.from_uri(file_uri=youtube_url, mime_type="video/mp4"),
            instruccion,
        ])

    if video is not None:
        download_url = video["download_url"]
        mime_type = video.get("mime_type") or "video/mp4"
        if not mime_type.startswith("video/"):
            raise ValueError("El archivo adjunto no es un video.")
        file_name = video.get("file_name") or "video.mp4"
        suffix = os.path.splitext(file_name)[1] or ".mp4"
        local_path = None
        gemini_file = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                local_path = temp.name
            if client is None:
                raise RuntimeError("Falta GEMINI_API_KEY en Render.")
            download_openai_file(download_url, local_path)
            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(mime_type=mime_type),
            )
            gemini_file = wait_until_active(gemini_file)
            return generate_video([gemini_file, instruccion])

        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            if gemini_file and getattr(gemini_file, "name", None):
                try:
                    client.files.delete(name=gemini_file.name)
                except Exception:
                    pass

    raise ValueError("Adjunta un archivo MP4 o proporciona una URL de YouTube.")


register_transcriptor_tools(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok", "service": "Yaco Transcriptor", "version": "0.3.0",
                         "gemini_configured": client is not None})


# Keep existing SSE clients working alongside the mobile HTTP endpoint.
application = mcp.streamable_http_app()
application.router.routes.extend(mcp.sse_app().routes)

if __name__ == "__main__":
    uvicorn.run(application, host="0.0.0.0", port=PORT)

