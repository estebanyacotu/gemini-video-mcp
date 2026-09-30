import os
import re
import tempfile
import time
import urllib.request
from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP

# Configuración de puerto y modelo
PORT = int(os.environ.get("PORT", 8000))
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

mcp = FastMCP("Gemini Video Analyzer", port=PORT, host="0.0.0.0")

# Cliente oficial de Google GenAI
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
    http_options={'timeout': 300}
)

def extract_youtube_id(url: str) -> str | None:
    """Extrae de forma robusta el ID de 11 caracteres de cualquier URL de YouTube."""
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
    """Analiza un video MP4 adjunto en ChatGPT o una URL pública de YouTube usando Gemini."""
    local_path = None
    gemini_file = None

    try:
        # Detectar URL de descarga para archivo adjunto (MP4)
        download_url = None
        if isinstance(video, dict):
            download_url = video.get("download_url") or video.get("url")
        elif isinstance(video, str) and video.startswith("http") and not ("youtube.com" in video or "youtu.be" in video):
            download_url = video

        # -------------------------------------------------------------
        # RUTA 1: Archivo MP4 adjunto (Files API)
        # -------------------------------------------------------------
        if download_url:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp:
                local_path = temp.name

            # Descargar archivo temporal
            urllib.request.urlretrieve(download_url, local_path)

            # Subir a la Files API de Gemini
            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(mime_type="video/mp4")
            )

            # Esperar procesamiento del video
            for _ in range(30):
                state = getattr(getattr(gemini_file, "state", None), "name", None)
                if state == "ACTIVE":
                    break
                if state in {"FAILED", "ERROR"}:
                    raise RuntimeError("Gemini no pudo procesar este archivo de video.")
                time.sleep(2)
                gemini_file = client.files.get(name=gemini_file.name)

            response = client.models.generate_content(
                model=MODEL,
                contents=[gemini_file, instruccion]
            )
            return response.text if response.text else "No se obtuvo respuesta del modelo."

        # -------------------------------------------------------------
        # RUTA 2: Enlace de YouTube (Ingesta nativa por URL)
        # -------------------------------------------------------------
        target_url = url or (isinstance(video, str) and video)
        if target_url:
            v_id = extract_youtube_id(target_url)
            if not v_id:
                return f"Error: No se reconoció una URL válida de YouTube: {target_url}"

            canonical_url = f"https://www.youtube.com/watch?v={v_id}"

            # Llamada directa sin scraping intermedio
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_uri(
                        file_uri=canonical_url,
                        mime_type="video/*"
                    ),
                    instruccion
                ]
            )
            return response.text if response.text else "No se obtuvo respuesta del modelo para el video de YouTube."

        return "Por favor adjunta un archivo de video MP4 o ingresa un enlace de YouTube."

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            return "Aviso de cuota (Error 429): El video excede el límite de tokens por minuto del nivel gratuito de Gemini. Espera un minuto o prueba con un video más corto."
        return f"Error al procesar con Gemini: {error_msg}"

    finally:
        # Limpieza de recursos
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
