# Gemini Video MCP + Yaco Transcriptor

Render-hosted MCP server for mobile and desktop ChatGPT.

## Endpoints

- `POST /mcp` — MCP Streamable HTTP
- The server listens on Render's `PORT`.

## Tools

The original `analizar_video` Gemini tool remains available.

Yaco Transcriptor compatibility adds:

- `get_transcript`
- `get_raw_subtitles`
- `get_available_subtitles`
- `get_video_info`
- `get_video_chapters`
- `get_video_frame`
- `get_playlist_transcripts`
- `search_videos`

## Runtime

Render runs `python app.py`. Video extraction uses the Python `yt-dlp` package.
Frame capture uses the bundled ffmpeg binary from `imageio-ffmpeg`.

Recommended environment values:

- `YT_DLP_MAX_CONCURRENCY=3`
- `YT_DLP_MAX_QUEUE=6`
- `YT_DLP_TIMEOUT=60`

