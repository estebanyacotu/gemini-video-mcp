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
        self.assertEqual(transcriptor._decode_cursor("1234"), 1234)
        with self.assertRaises(ValueError):
            transcriptor._decode_cursor("not-a-cursor")

    def test_pagination_defaults_and_bounds(self):
        text = "x" * 60000
        chunk, start, end, cursor = transcriptor._page(text, None, None)
        self.assertEqual((len(chunk), start, end), (60000, 0, 60000))
        self.assertIsNone(cursor)

        chunk, start, end, cursor = transcriptor._page(text, 5000, None)
        self.assertEqual((len(chunk), start, end, cursor), (5000, 0, 5000, "5000"))

        with self.assertRaises(ValueError):
            transcriptor._page(text, 999, None)

    def test_tool_contract_and_annotations(self):
        tools = asyncio.run(app.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
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
        self.assertTrue(expected.issubset(by_name))
        for name in expected - {"analizar_video"}:
            annotations = by_name[name].annotations
            dumped = annotations.model_dump(by_alias=True) if hasattr(annotations, "model_dump") else dict(annotations)
            self.assertTrue(dumped.get("readOnlyHint"))
            self.assertFalse(dumped.get("destructiveHint"))
            self.assertTrue(dumped.get("openWorldHint"))
            if name == "search_videos":
                self.assertFalse(dumped.get("idempotentHint"))
            else:
                self.assertTrue(dumped.get("idempotentHint"))


if __name__ == "__main__":
    unittest.main()
