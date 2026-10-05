import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


VIDEO_URL = "https://www.youtube.com/live/v3-odNpotVc?si=cNuv2i5DH4GOkDqr"
EXPECTED_TOOLS = {
    "get_transcript",
    "get_raw_subtitles",
    "get_available_subtitles",
    "get_video_info",
    "get_video_chapters",
    "get_video_frame",
    "get_playlist_transcripts",
    "search_videos",
}


def structured(result):
    value = getattr(result, "structuredContent", None)
    if value:
        return value
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"No structured JSON in tool result: {result!r}")


async def main():
    url = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")
    async with streamable_http_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            assert init.serverInfo.name

            tools = await session.list_tools()
            by_name = {tool.name: tool for tool in tools.tools}
            missing = EXPECTED_TOOLS - set(by_name)
            assert not missing, f"Missing tools: {sorted(missing)}"

            for name in EXPECTED_TOOLS:
                annotations = by_name[name].annotations
                dumped = annotations.model_dump(by_alias=True) if hasattr(annotations, "model_dump") else {}
                assert dumped.get("readOnlyHint") is True, (name, dumped)
                assert dumped.get("destructiveHint") is False, (name, dumped)
                assert dumped.get("openWorldHint") is True, (name, dumped)

            info_result = await session.call_tool("get_video_info", {"url": VIDEO_URL})
            assert not info_result.isError, info_result
            info = structured(info_result)
            assert info.get("videoId") == "v3-odNpotVc", info
            assert info.get("title"), info

            tracks_result = await session.call_tool(
                "get_available_subtitles", {"url": VIDEO_URL}
            )
            assert not tracks_result.isError, tracks_result
            tracks = structured(tracks_result)
            assert "es-orig" in (tracks.get("auto") or []), tracks

            transcript_result = await session.call_tool(
                "get_transcript",
                {
                    "url": VIDEO_URL,
                    "type": "auto",
                    "lang": "es-orig",
                    "response_limit": 5000,
                },
            )
            assert not transcript_result.isError, transcript_result
            transcript = structured(transcript_result)
            text = transcript.get("text") or ""
            assert len(text) >= 500, transcript
            assert transcript.get("lang") == "es-orig", transcript
            assert transcript.get("type") == "auto", transcript
            assert transcript.get("next_cursor"), transcript
            assert transcript.get("is_truncated") is True, transcript
            assert transcript.get("end_offset") == 5000, transcript

            page2_result = await session.call_tool(
                "get_transcript",
                {
                    "url": VIDEO_URL,
                    "type": "auto",
                    "lang": "es-orig",
                    "response_limit": 5000,
                    "next_cursor": transcript["next_cursor"],
                },
            )
            assert not page2_result.isError, page2_result
            page2 = structured(page2_result)
            assert page2.get("start_offset") == 5000, page2
            assert page2.get("text"), page2

            print(
                json.dumps(
                    {
                        "server": init.serverInfo.name,
                        "tools": sorted(EXPECTED_TOOLS),
                        "videoId": info["videoId"],
                        "title": info["title"],
                        "has_es_orig": True,
                        "page1_chars": len(text),
                        "page1_next_cursor": True,
                        "page2_chars": len(page2.get("text") or ""),
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
