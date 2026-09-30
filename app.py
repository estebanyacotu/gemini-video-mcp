import os
import time
import tempfile
import urllib.request
import logging
import re
from urllib.parse import parse_qs, urlsplit

from typing_extensions import TypedDict, Required, NotRequired

from mcp.server.fastmcp import FastMCP
from google import genai
from google.genai import types


# ---------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------

PORT = int(os.environ.get("PORT", 8000))
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Gemini Video Analyzer",
    port=PORT,
    host="0.0.0.0",
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY"),
    http_options=types.HttpOptions(
        timeout=30_000,
        retry_options=types.HttpRetryOptions(
            attempts=3,
            initial_delay=2.0,
            max_delay=15.0,
            exp_base=2.0,
            jitter=1.0,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    ),
)


# ---------------------------------------------------------
# ESQUEMA OFICIAL DE ARCHIVO DE OPENAI
# ---------------------------------------------------------

class OpenAIFile(TypedDict):
    download_url: Required[str]
    file_id: Required[str]
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


def normalize_youtube_url(url: str) -> str:
    """Validar el dominio real y normalizar watch, live, shorts y youtu.be."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        raise ValueError("Introduce una URL pública válida de YouTube.")

    host = (parsed.hostname or "").lower()
    segments = parsed.path.strip("/").split("/")
    if host == "youtu.be":
        video_id = segments[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(segments) == 2 and segments[0] in {"live", "shorts", "embed"}:
            video_id = segments[1]
        else:
            raise ValueError("La URL debe apuntar a un video de YouTube.")
    else:
        raise ValueError("Este campo admite enlaces de YouTube; adjunta otros videos como archivo.")

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("El enlace de YouTube no contiene un ID de video válido.")
    return "https://www.youtube.com/watch?v=" + video_id


# ---------------------------------------------------------
# DESCARGAR ARCHIVO DE CHATGPT
# ---------------------------------------------------------

def download_openai_file(
    download_url: str,
    destination: str,
    max_bytes: int = 500 * 1024 * 1024,
):
    request = urllib.request.Request(
        download_url,
        headers={
            "User-Agent": "Gemini-Video-Plugin/1.0"
        },
    )

    total = 0

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:

        with open(destination, "wb") as output:

            while True:
                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                total += len(chunk)

                if total > max_bytes:
                    raise ValueError(
                        "El archivo supera el límite permitido."
                    )

                output.write(chunk)


# ---------------------------------------------------------
# ESPERAR A GEMINI
# ---------------------------------------------------------

def wait_until_active(
    gemini_file,
    timeout_seconds: int = 90,
):
    deadline = time.monotonic() + timeout_seconds

    while True:

        state = getattr(
            getattr(gemini_file, "state", None),
            "name",
            None,
        )

        if state == "ACTIVE":
            return gemini_file

        if state in {
            "FAILED",
            "ERROR",
            "CANCELLED",
        }:
            raise RuntimeError(
                f"Gemini no pudo procesar el video. "
                f"Estado: {state}"
            )

        if state != "PROCESSING":
            raise RuntimeError(f"Estado de archivo inesperado en Gemini: {state}")

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Gemini tardó demasiado procesando el video."
            )

        time.sleep(min(3, max(0, deadline - time.monotonic())))
        gemini_file = client.files.get(name=gemini_file.name)


# ---------------------------------------------------------
# TOOL PRINCIPAL
# ---------------------------------------------------------

@mcp.tool(
    meta={
        "openai/fileParams": ["video"]
    }
)
def analizar_video(
    video: OpenAIFile | None = None,
    url: str | None = None,
    instruccion: str = (
        "Analiza detalladamente este video. "
        "Identifica puntos clave, marcas de tiempo, "
        "transcripción relevante y mejores momentos."
    ),
) -> str:
    """
    Analiza un MP4 adjunto desde ChatGPT
    o una URL pública de YouTube usando Gemini.
    """

    local_path = None
    gemini_file = None
    stage = "validación de entrada"

    try:

        if video is not None and url:
            raise ValueError("Envía un archivo adjunto o un enlace, uno por solicitud.")

        # =================================================
        # CASO 1 — MP4 ADJUNTO EN CHATGPT
        # =================================================

        if video:

            download_url = video["download_url"]

            file_name = video.get(
                "file_name",
                "video.mp4",
            )

            suffix = os.path.splitext(file_name)[1]

            if not suffix:
                suffix = ".mp4"

            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            ) as temp:
                local_path = temp.name

            # Descargar archivo temporal de OpenAI
            stage = "descarga del archivo adjunto"
            download_openai_file(
                download_url,
                local_path,
            )

            # Subir archivo a Gemini
            stage = "subida del archivo a Gemini"
            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(
                    mime_type=video.get("mime_type") or "video/mp4"
                ),
            )

            # Esperar hasta que Gemini termine de procesarlo
            stage = "procesamiento del archivo en Gemini"
            gemini_file = wait_until_active(
                gemini_file
            )

            # Analizar video + audio
            stage = "análisis del video en Gemini"
            response = client.models.generate_content(
                model=MODEL,
                contents=[
                    gemini_file,
                    instruccion,
                ],
            )

            if not response.text:
                raise RuntimeError("Gemini no devolvió texto de análisis.")
            return response.text


        # =================================================
        # CASO 2 — URL DE YOUTUBE
        # =================================================

        if url:

            video_url = normalize_youtube_url(url)

            stage = "análisis del enlace de YouTube en Gemini"
            interaction = client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "text",
                        "text": instruccion,
                    },
                    {
                        "type": "video",
                        "uri": video_url,
                    },
                ],
            )

            if not interaction.output_text:
                raise RuntimeError("Gemini no devolvió texto de análisis.")
            return interaction.output_text


        raise ValueError(
            "No se detectó un video adjunto "
            "ni una URL de YouTube."
        )


    except (ValueError, TimeoutError):
        raise
    except Exception as error:
        code = getattr(error, "code", None)
        logger.warning("Fallo en %s: %s, código=%s", stage, type(error).__name__, code)
        if code == 503:
            message = "Gemini sigue temporalmente no disponible tras los reintentos. Prueba más tarde."
        elif code == 429:
            message = "Gemini devolvió 429 tras los reintentos. Revisa la cuota y los límites de tu cuenta."
        else:
            message = f"Fallo en {stage} ({type(error).__name__}, código={code})."
        # Una excepción produce isError=true en MCP, en lugar de un éxito falso.
        raise RuntimeError(message) from None


    finally:

        # Borrar copia temporal del servidor
        if local_path and os.path.exists(local_path):

            try:
                os.remove(local_path)

            except Exception as error:
                logger.warning("No se pudo borrar el temporal local: %s", type(error).__name__)


        # Borrar copia subida a Gemini
        if (
            gemini_file
            and getattr(gemini_file, "name", None)
        ):

            try:
                client.files.delete(
                    name=gemini_file.name
                )

            except Exception as error:
                logger.warning("No se pudo borrar la copia en Gemini: %s", type(error).__name__)


# ---------------------------------------------------------
# SERVER
# ---------------------------------------------------------

if __name__ == "__main__":
    mcp.run(
        transport="sse"
    )
