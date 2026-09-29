import os
import time
import tempfile
import urllib.request
from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types

port = int(os.environ.get("PORT", 8000))
mcp = FastMCP("Gemini Video Analyzer", port=port, host="0.0.0.0")

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: dict = None,
    url: str = None,
    instruccion: str = "Analiza detalladamente los puntos clave, marcas de tiempo y mejores momentos del video."
) -> str:
    """Analiza un video adjunto en ChatGPT (MP4) o una URL de YouTube usando Gemini Multimodal."""
    target_file_path = None
    gemini_file = None

    try:
        # Detectar si ChatGPT entregó un archivo adjunto
        download_url = None
        if isinstance(video, dict):
            download_url = video.get("download_url") or video.get("url")
        elif isinstance(video, str) and video.startswith("http"):
            download_url = video

        # CASO 1: Archivo MP4 subido desde el celular en ChatGPT
        if download_url:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            target_file_path = tmp.name
            tmp.close()

            # Descargar archivo temporal
            urllib.request.urlretrieve(download_url, target_file_path)

            # Subir a la Files API de Gemini
            gemini_file = client.files.upload(file=target_file_path)

            # Esperar procesamiento de Gemini
            while hasattr(gemini_file, 'state') and getattr(gemini_file.state, 'name', '') == "PROCESSING":
                time.sleep(2)
                gemini_file = client.files.get(name=gemini_file.name)

            # Analizar video completo (imagen + audio)
            res = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[gemini_file, instruccion]
            )
            return res.text

        # CASO 2: Enlace de YouTube o video web
        video_url = url or (isinstance(video, str) and not video.startswith("{") and video)
        if video_url:
            # Normalizar directos o enlaces cortos
            if "youtube.com/live/" in video_url:
                video_url = video_url.replace("youtube.com/live/", "youtube.com/watch?v=")
            elif "youtu.be/" in video_url:
                v_id = video_url.split("youtu.be/").split("?")[0]
                video_url = f"https://www.youtube.com/watch?v={v_id}"

            try:
                response = client.interactions.create(
                    model="gemini-3.8-flash",
                    input=[
                        {"type": "text", "text": instruccion},
                        {"type": "video", "uri": video_url, "mime_type": "video/*"}
                    ]
                )
                if hasattr(response, 'output_text') and response.output_text:
                    return response.output_text
            except Exception:
                pass

            res = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_uri(file_uri=video_url, mime_type="video/*"),
                    instruccion
                ]
            )
            return res.text

        return "No se detectó ningún archivo de video adjunto ni enlace para analizar."

    except Exception as e:
        return f"Error procesando video con Gemini: {str(e)}"
    finally:
        # Limpiar archivo temporal local
        if target_file_path and os.path.exists(target_file_path):
            try:
                os.remove(target_file_path)
            except Exception:
                pass
        # Limpiar archivo en los servidores de Gemini
        if gemini_file and hasattr(gemini_file, 'name'):
            try:
                client.files.delete(name=gemini_file.name)
            except Exception:
                pass

if __name__ == "__main__":
    mcp.run(transport="sse")
