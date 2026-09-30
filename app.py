import os
import re
import tempfile
import time
import urllib.request
from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("PORT", 8000))
MODEL = "gemini-3.8-flash"

mcp = FastMCP("Gemini Video Analyzer", port=PORT, host="0.0.0.0")

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
    http_options={'timeout': 300}
)

def extract_youtube_id(url: str) -> str | None:
    if not url:
        return None
    match = re.search(r'(?:v=|\/|youtu\.be\/|embed\/|shorts\/|live\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else None

@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: dict = None,
    url: str = None,
    instruccion: str = "Analiza detalladamente este video. Identifica los puntos clave, marcas de tiempo y mejores momentos."
) -> str:
    local_path = None
    gemini_file = None

    try:
        download_url = None
        if isinstance(video, dict):
            download_url = video.get("download_url") or video.get("url")
        elif isinstance(video, str) and video.startswith("http") and not ("youtube.com" in video or "youtu.be" in video):
            download_url = video

        # 1. Archivo MP4
        if download_url:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp:
                local_path = temp.name

            urllib.request.urlretrieve(download_url, local_path)

            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(mime_type="video/mp4")
            )

            for _ in range(30):
                state = getattr(getattr(gemini_file, "state", None), "name", None)
                if state == "ACTIVE":
                    break
                if state in {"FAILED", "ERROR"}:
                    raise RuntimeError("Gemini no pudo procesar este archivo de video.")
                time.sleep(2)
                gemini_file = client.files.get(name=gemini_file.name)

            interaction = client.interactions.create(
                model=MODEL,
                input=[
                    {"type": "text", "text": instruccion},
                    {"type": "video", "uri": gemini_file.uri}
                ]
            )
            return interaction.output_text if getattr(interaction, "output_text", None) else "No se obtuvo respuesta del modelo."

        # 2. YouTube URL
        target_url = url or (isinstance(video, str) and video)
        if target_url:
            v_id = extract_youtube_id(target_url)
            if not v_id:
                return f"Error: URL no válida de YouTube: {target_url}"

            canonical_url = f"https://www.youtube.com/watch?v={v_id}"

            interaction = client.interactions.create(
                model=MODEL,
                input=[
                    {"type": "text", "text": instruccion},
                    {"type": "video", "uri": canonical_url}
                ]
            )
            return interaction.output_text if getattr(interaction, "output_text", None) else "No se obtuvo respuesta del modelo."

        return "Por favor adjunta un archivo MP4 o ingresa un enlace de YouTube."

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            return "Error 429: Se excedió el límite de cuota. Espera un momento antes de reintentar."
        return f"Error al procesar con Gemini: {error_msg}"

    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass
        if gemini_file and getattr(gemini_file, "name", None):
            try:
                client.files.delete(name=gemini_file.name)
            except Exception:
                pass

if __name__ == "__main__":
    mcp.run(transport="sse")
