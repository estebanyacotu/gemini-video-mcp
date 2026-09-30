import os
import tempfile
import time
import urllib.request
from typing import TypedDict
from typing_extensions import Required, NotRequired
from urllib.parse import urlparse, parse_qs

from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP


PORT = int(os.environ.get("PORT", 8000))
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

mcp = FastMCP(
    "Gemini Video Analyzer",
    port=PORT,
    host="0.0.0.0",
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY")
)


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

    elif host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    }:
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]

        elif (
            len(parts) >= 2
            and parts[0] in {"shorts", "live", "embed"}
        ):
            video_id = parts[1]

    else:
        raise ValueError("La URL no pertenece a YouTube.")

    if not video_id or len(video_id) != 11:
        raise ValueError(
            "No se pudo obtener un ID válido de YouTube."
        )

    return f"https://www.youtube.com/watch?v={video_id}"


def wait_until_active(
    gemini_file,
    timeout_seconds: int = 120,
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

        if state in {"FAILED", "ERROR"}:
            raise RuntimeError(
                "Gemini no pudo procesar el video."
            )

        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Gemini tardó demasiado en procesar el video."
            )

        time.sleep(2)

        gemini_file = client.files.get(
            name=gemini_file.name
        )


@mcp.tool(meta={"openai/fileParams": ["video"]})
def analizar_video(
    video: OpenAIFile | None = None,
    url: str | None = None,
    instruccion: str = (
        "Analiza detalladamente este video. "
        "Identifica los puntos clave, marcas de tiempo "
        "y mejores momentos."
    ),
) -> str:

    if video is not None and url:
        raise ValueError(
            "Envía un MP4 o una URL de YouTube, no ambos."
        )

    # =====================================================
    # RUTA 1 — YOUTUBE
    # =====================================================

    if url:
        youtube_url = normalize_youtube_url(url)

        interaction = client.interactions.create(
            model=MODEL,
            input=[
                {
                    "type": "video",
                    "uri": youtube_url,
                },
                {
                    "type": "text",
                    "text": instruccion,
                },
            ],
        )

        if not getattr(interaction, "output_text", None):
            raise RuntimeError(
                "Gemini respondió sin texto."
            )

        return interaction.output_text

    # =====================================================
    # RUTA 2 — MP4 ADJUNTO DESDE CHATGPT
    # =====================================================

    if video is not None:

        download_url = video["download_url"]
        mime_type = video.get("mime_type") or "video/mp4"

        if not mime_type.startswith("video/"):
            raise ValueError(
                "El archivo adjunto no es un video."
            )

        file_name = video.get("file_name") or "video.mp4"

        suffix = os.path.splitext(file_name)[1] or ".mp4"

        local_path = None
        gemini_file = None

        try:

            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            ) as temp:
                local_path = temp.name

            request = urllib.request.Request(
                download_url,
                headers={
                    "User-Agent":
                    "Gemini-Video-Analyzer/1.0"
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=120,
            ) as response:
                with open(local_path, "wb") as output:
                    output.write(response.read())

            gemini_file = client.files.upload(
                file=local_path,
                config=types.UploadFileConfig(
                    mime_type=mime_type
                ),
            )

            gemini_file = wait_until_active(
                gemini_file
            )

            interaction = client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "video",
                        "uri": gemini_file.uri,
                        "mime_type":
                            gemini_file.mime_type
                            or mime_type,
                    },
                    {
                        "type": "text",
                        "text": instruccion,
                    },
                ],
            )

            if not getattr(
                interaction,
                "output_text",
                None,
            ):
                raise RuntimeError(
                    "Gemini respondió sin texto."
                )

            return interaction.output_text

        finally:

            if (
                local_path
                and os.path.exists(local_path)
            ):
                try:
                    os.remove(local_path)
                except OSError:
                    pass

            if (
                gemini_file
                and getattr(
                    gemini_file,
                    "name",
                    None,
                )
            ):
                try:
                    client.files.delete(
                        name=gemini_file.name
                    )
                except Exception:
                    pass

    raise ValueError(
        "Adjunta un archivo MP4 "
        "o proporciona una URL de YouTube."
    )


if __name__ == "__main__":
    mcp.run(transport="sse")