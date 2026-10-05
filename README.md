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

## Plugin 0.3.1 candidate / server 0.3.0: conversation first

A bare video URL starts a conversation about its content. Answer the user's question or offer a brief content-grounded explanation. Only an explicit clipping request activates candidate selection, ranking and packaging. Uploaded videos receive general audiovisual analysis; final clips receive editorial review when requested or established by the active task.

Transcription/metadata/frame tools accept the 11 platform families documented by [Transcriptor](https://github.com/samson-art/transcriptor-mcp): YouTube, X/Twitter, Instagram, TikTok, Twitch, Vimeo, Facebook, Bilibili, VK, Dailymotion and Reddit. Acceptance is not evidence that every link works. Audiovisual URL analysis and the block-based clipping workflow currently require YouTube; other platforms can use an attached video for audiovisual analysis. Search is YouTube-only.

Routing examples:
- A bare YouTube link or "what does this Instagram reel say?" → grounded conversation, no clipping package.
- An attached lecture → general audiovisual analysis, no assumed production task.
- "Vamos a hacer los clips" → exhaustive candidate pass and editing package.
- "Evalúa este corte final" with an attachment → timestamped editorial review.
- A new unrelated video after a clipping task → honor the new request; do not automatically carry clipping intent forward.

### Live cuts and mobile clip review

New tools:
- `preparar_live(url)`: verified duration and complete 8-minute windows, with 20-second overlap.
- `analizar_bloque_live(url, block, language?)`: audio + sampled video (1 fps), subtitles when available; otherwise estimated speech transcription. Validates JSON/timestamp bounds and labels quote matches. Returns candidate assembly segments and editorial packaging. Call every block before ranking.
- `evaluar_clip(video, objetivo?)`: host attachment input; audio + video sampled at 2 fps; timestamped revision report and editorial score. Does not predict commercial performance.

`plugin/yaco-transcriptor` contains the overlay for the existing private plugin, preserving its identity and default prompts. **It is a release candidate, not an installed update.** The URL targets the existing Render service. Do not upload this overlay before deploying and verifying all 12 MCP tools there. Existing omitted package assets are preserved by Plugin Creator overlays.

Runtime: `GEMINI_API_KEY` is required; `GEMINI_MODEL` defaults to `gemini-3.8-flash`. Dependencies pin the SDK used for Interactions static video processing. No keys belong in the plugin archive or Git. Public completed YouTube videos only; Google/YouTube availability, quota and caption blocking can prevent analysis. Metadata failure blocks duration verification rather than manufacturing a duration. Gemini results and timestamps are model judgments, not guaranteed frame accuracy. Clip uploads are bounded to 200 MiB, use host storage HTTPS URLs, delete local files and attempt remote-file cleanup, and set Interactions `store=False`.

### Verification and release

1. `GEMINI_API_KEY=test-key python -m unittest -v test_app test_transcriptor test_clipping` checks deterministic behavior with mocked providers (no claim of end-to-end Gemini success).
2. Confirm the Render workspace, inspect existing production/staging services and env key presence without revealing values, then deploy this branch to staging. Existing service build/start settings must be reused.
3. Verify `/mcp` discovery, test a public completed live including every planned block and coverage, then test an actual mobile attachment through ChatGPT. This is required to validate signed URL routing, model availability, quota, timeout behavior and native upload support.
4. Deploy production, verify version 0.3.0 responses and tool discovery, then use Plugin Creator update with the existing plugin ID and observed release ID. Read back both manifests/MCP configs/skill and verify identity and default prompts.
5. Rollback: redeploy the previous server commit and republish the previous plugin files with a new semantic version if needed. Never overwrite secrets or change sharing.

No video publishing, account monetization setup, or historical metrics are performed. Long videos can require multiple host turns; return exact remaining blocks if execution limits interrupt a pass. Retries may incur provider cost; returned `cost_usd` remains null because billing is not measured.
