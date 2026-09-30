import os
import time
import tempfile
import urllib.request
from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types

# Puerto de Render y modelo optimizado para cuenta gratuita
PORT = int(os.environ.get("PORT", 8000))
MODEL = "gemini-3-flash-preview"

mcp = FastMCP("Gemini Video Analyzer", port=PORT, host="0.0.0.0")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: dict = None,
    url: str = None,
    instruccion: str = "Analiza detalladamente este video. Identifica los puntos clave, marcas de tiempo y mejores momentos."
) -> str:
    """Analiza un video MP4 adjunto en ChatGPT o una URL de YouTube usando Gemini."""
    local_path = None
    gemini_file = None

    try:
        download_url = None
        if isinstance(video, dict):
            download_url = video.get("download_url") or video.get("url")
        elif isinstance(video, str) and video.startswith("http"):
            download_url = video

        # CASO 1: Archivo MP4 adjunto desde el celular
        if download_url:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp:
                local_path = temp.name

            # Descargar archivo temporal enviado por ChatGPT
            urllib.request.urlretrieve(download_url, local_path)

            # Subir a la Files API de Gemini
            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(mime_type="video/mp4")
            )

            # Esperar a que Gemini procese el archivo (máximo 60 segundos)
            for _ in range(30):
                state = getattr(getattr(gemini_file, "state", None), "name", None)
                if state == "ACTIVE":
                    break
                if state in {"FAILED", "ERROR"}:
                    raise RuntimeError("Gemini no pudo procesar este formato de video.")
                time.sleep(2)
                gemini_file = client.files.get(name=gemini_file.name)

            # Análisis multimodal del video
            try:
                response = client.models.generate_content(
                    model=MODEL,
                    contents=[gemini_file, instruccion]
                )
            except Exception as e:
                # Si el modelo preview da 429, reintenta con 3.8 flash
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=[gemini_file, instruccion]
                )

            return response.text if response.text else "No se pudo extraer texto del análisis."

        # CASO 2: Enlace de YouTube
        target_url = url or (isinstance(video, str) and video)
        if target_url:
            if "youtube.com/live/" in target_url:
                target_url = target_url.replace("youtube.com/live/", "youtube.com/watch?v=")
            elif "youtu.be/" in target_url:
                v_id = target_url.split("youtu.be/").split("?")[0]
                target_url = f"https://www.youtube.com/watch?v={v_id}"

            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_uri(file_uri=target_url, mime_type="video/*"),
                    instruccion
                ]
            )
            return response.text if response.text else "No se pudo generar el análisis."

        return "Por favor adjunta un archivo de video MP4 o ingresa un enlace de YouTube."

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            return "Aviso de cuota (Error 429): El video excede el límite de tokens por minuto del nivel gratuito de Gemini. Prueba con un video más corto (menos de 2-3 minutos) o espera un minuto antes de reintentar."
        return f"Error al procesar con Gemini: {error_msg}"

    finally:
        # Limpieza de archivos temporales
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
