import os
import re
import tempfile
import time
import urllib.request
from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import YouTubeTranscriptApi

# Puerto de Render y configuración
PORT = int(os.environ.get("PORT", 8000))
MODEL = "gemini-3.8-flash"

mcp = FastMCP("Gemini Video Analyzer", port=PORT, host="0.0.0.0")
client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"), http_options={"timeout": 300}
)


def extract_youtube_id(url: str) -> str | None:
  """Extrae de forma segura el ID de YouTube sin usar split."""
  if not url:
    return None
  match = re.search(
      r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/|live\/)([0-9A-Za-z_-]{11})", url
  )
  return match.group(1) if match else None


@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: dict = None,
    url: str = None,
    instruccion: str = (
        "Analiza detalladamente este video. Identifica los puntos clave, marcas"
        " de tiempo y mejores momentos."
    ),
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

      urllib.request.urlretrieve(download_url, local_path)

      gemini_file = client.files.upload(
          file=local_path, config=types.UploadFileConfig(mime_type="video/mp4")
      )

      for _ in range(30):
        state = getattr(getattr(gemini_file, "state", None), "name", None)
        if state == "ACTIVE":
          break
        if state in {"FAILED", "ERROR"}:
          raise RuntimeError("Gemini no pudo procesar este formato de video.")
        time.sleep(2)
        gemini_file = client.files.get(name=gemini_file.name)

      response = client.models.generate_content(
          model=MODEL, contents=[gemini_file, instruccion]
      )
      return (
          response.text
          if response.text
          else "No se pudo extraer texto del análisis."
      )

    # CASO 2: Enlace de YouTube
    target_url = url or (isinstance(video, str) and video)
    if target_url:
      v_id = extract_youtube_id(target_url)
      if not v_id:
        return f"Error: No se pudo reconocer una URL válida de YouTube: {target_url}"

      canonical_url = f"https://www.youtube.com/watch?v={v_id}"

      # Intento multimodal nativo
      try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=canonical_url, mime_type="video/*"
                ),
                instruccion,
            ],
        )
        return (
            response.text
            if response.text
            else "No se pudo generar el análisis."
        )
      except Exception:
        # Respaldo por transcripción para evitar cuotas o timeouts
        try:
          transcript = YouTubeTranscriptApi.get_transcript(
              v_id, languages=["es", "es-419", "en"]
          )
          texto = " ".join([t["text"] for t in transcript])
          response = client.models.generate_content(
              model=MODEL,
              contents=[
                  f"Transcripción del video ({canonical_url}):\n\n{texto[:45000]}",
                  instruccion,
              ],
          )
          return response.text
        except Exception:
          raise

    return (
        "Por favor adjunta un archivo de video MP4 o ingresa un enlace de"
        " YouTube."
    )

  except Exception as e:
    error_msg = str(e)
    if "429" in error_msg:
      return (
          "Aviso de cuota (Error 429): El video excede el límite de tokens por"
          " minuto del nivel gratuito de Gemini. Prueba con un video más corto"
          " o espera un minuto antes de reintentar."
      )
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
