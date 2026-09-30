import os
import time
import tempfile
import urllib.request

from typing import TypedDict
from typing_extensions import Required, NotRequired

from mcp.server.fastmcp import FastMCP
from google import genai


# ---------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------

PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "Gemini Video Analyzer",
    port=PORT,
    host="0.0.0.0",
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY")
)


# ---------------------------------------------------------
# ESQUEMA OFICIAL DE ARCHIVO DE OPENAI
# ---------------------------------------------------------

class OpenAIFile(TypedDict):
    download_url: Required[str]
    file_id: Required[str]
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


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
        timeout=120,
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
    timeout_seconds: int = 600,
):
    start = time.time()

    while True:

        gemini_file = client.files.get(
            name=gemini_file.name
        )

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

        if time.time() - start > timeout_seconds:
            raise TimeoutError(
                "Gemini tardó demasiado procesando el video."
            )

        time.sleep(3)


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

    try:

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
            download_openai_file(
                download_url,
                local_path,
            )

            # Subir archivo a Gemini
            gemini_file = client.files.upload(
                file=local_path
            )

            # Esperar hasta que Gemini termine de procesarlo
            gemini_file = wait_until_active(
                gemini_file
            )

            # Analizar video + audio
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    gemini_file,
                    instruccion,
                ],
            )

            return response.text or (
                "Gemini procesó el video pero "
                "no devolvió texto."
            )


        # =================================================
        # CASO 2 — URL DE YOUTUBE
        # =================================================

        if url:

            video_url = url.strip()

            # Normalizar enlaces cortos
            if "youtu.be/" in video_url:

                video_id = (
                    video_url
                    .split("youtu.be/", 1)[1]
                    .split("?", 1)[0]
                )

                video_url = (
                    "https://www.youtube.com/watch?v="
                    + video_id
                )

            elif "youtube.com/live/" in video_url:

                video_id = (
                    video_url
                    .split("youtube.com/live/", 1)[1]
                    .split("?", 1)[0]
                )

                video_url = (
                    "https://www.youtube.com/watch?v="
                    + video_id
                )

            interaction = client.interactions.create(
                model="gemini-3.8-flash",
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

            return (
                interaction.output_text
                or "Gemini no devolvió texto."
            )


        return (
            "No se detectó un video adjunto "
            "ni una URL de YouTube."
        )


    except Exception as error:

        return (
            "Error procesando video con Gemini: "
            f"{type(error).__name__}: {error}"
        )


    finally:

        # Borrar copia temporal del servidor
        if local_path and os.path.exists(local_path):

            try:
                os.remove(local_path)

            except Exception:
                pass


        # Borrar copia subida a Gemini
        if (
            gemini_file
            and getattr(gemini_file, "name", None)
        ):

            try:
                client.files.delete(
                    name=gemini_file.name
                )

            except Exception:
                pass


# ---------------------------------------------------------
# SERVER
# ---------------------------------------------------------

if __name__ == "__main__":
    mcp.run(
        transport="sse"
    )