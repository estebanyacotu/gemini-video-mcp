import asyncio
import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-a-real-credential")

import app
import transcriptor


class TranscriptorContractTests(unittest.TestCase):
    def test_normalize_youtube_id(self):
        self.assertEqual(
            transcriptor._normalize_url("dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_reject_unsupported_host(self):
        with self.assertRaises(ValueError):
            transcriptor._normalize_url("https://example.com/video")

    def test_cursor_roundtrip(self):
        parts = {"url": "https://youtu.be/dQw4w9WgXcQ", "lang": "en"}
        cursor = transcriptor._encode_cursor(1234, parts)
        self.assertEqual(transcriptor._decode_cursor(cursor, parts), 1234)

    def test_tool_contract(self):
        tools = asyncio.run(app.mcp.list_tools())
        names = {tool.name for tool in tools}
        expected = {
            "analizar_video",
            "get_transcript",
            "get_raw_subtitles",
            "get_available_subtitles",
            "get_video_info",
            "get_video_chapters",
            "get_video_frame",
            "get_playlist_transcripts",
            "search_videos",
        }
        self.assertTrue(expected.issubset(names))


if __name__ == "__main__":
    unittest.main()
