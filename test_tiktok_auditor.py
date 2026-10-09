import unittest
import json
import subprocess
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tiktok_auditor as t
from mcp.server.fastmcp import FastMCP


class TikTokAuditorTests(unittest.TestCase):
    def test_tikwm_rejects_non_object_json_and_accepts_relative_media(self):
        for payload in [b"[]", b"null", b"123"]:
            with self.assertRaises(t.TikTokError):
                t.parse_tikwm(payload)
        url, _ = t.parse_tikwm(b'{"code":0,"data":{"play":"/video/example.mp4"}}')
        self.assertEqual(url, "https://www.tikwm.com/video/example.mp4")

    def test_provider_failure_is_not_claimed_as_absent_captions(self):
        with patch.object(t, "obtain_tiktok", return_value=(Path("unused"), {
            "resolved_url": None, "requested_url": "https://vt.tiktok.com/example/"
        })), patch("transcriptor._subtitle_payload", side_effect=RuntimeError("HTTP 429")), \
                patch.dict(t.os.environ, {"LOCAL_ASR": "0"}):
            result = t.captions_tiktok("https://vt.tiktok.com/example/")
        self.assertEqual(result["status"], "transcripcion_no_obtenida")
        self.assertIn("429", result["details"])

    def test_real_media_without_ffprobe_and_native_mcp_image(self):
        with TemporaryDirectory() as temp:
            clip = Path(temp) / "sample.mp4"
            subprocess.run([t.imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi",
                            "-i", "color=c=red:s=320x240:d=2", "-c:v", "libx264", "-y", str(clip)],
                           check=True, timeout=20, capture_output=True)
            with patch.object(t.shutil, "which", return_value=None):
                technical = t._probe(clip)
            self.assertAlmostEqual(technical["duration_seconds"], 2, places=1)
            self.assertEqual(technical["streams"][0]["width"], 320)
            info = {"requested_url": "https://vt.tiktok.com/example/", "technical": technical}
            mcp = FastMCP("test", stateless_http=True, json_response=True)
            t.register_tiktok_tools(mcp)
            # Exercise the actual HTTP MCP handler, not only the Python return value.
            from starlette.testclient import TestClient
            with patch.object(t, "obtain_tiktok", return_value=(clip, info)):
                with TestClient(mcp.streamable_http_app(), base_url="http://localhost:8000") as client:
                    response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                 "params": {"name": "fotograma_tiktok", "arguments": {
                                                     "url": info["requested_url"], "seconds": 0.5, "width": 320}}})
                self.assertEqual(response.status_code, 200)
                result = response.json()["result"]
                self.assertFalse(result.get("isError", False))
                self.assertEqual([b["type"] for b in result["content"]], ["text", "image"])
                self.assertEqual(result["content"][1]["mimeType"], "image/jpeg")
                self.assertEqual(json.loads(result["content"][0]["text"])["seconds"], 0.5)
                with self.assertRaises(ValueError):
                    t.capture_tiktok_frame(info["requested_url"], 2)

    def test_cache_reader_holds_lock_against_other_threads(self):
        observed = []
        def other_thread():
            acquired = t._LOCK.acquire(blocking=False)
            observed.append(acquired)
            if acquired:
                t._LOCK.release()
        @t.uses_cached_media
        def reader():
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(timeout=2)
        reader()
        self.assertEqual(observed, [False])

    def test_invalid_source_and_api_urls_are_rejected(self):
        for url in ["http://vt.tiktok.com/x", "https://vt.tiktok.com.evil.org/x", "https://localhost/x"]:
            with self.assertRaises(ValueError):
                t.normalize_url(url)
        for payload in [
            b'{"code":0,"data":{"play":"http://127.0.0.1/test.mp4"}}',
            b'{"code":0,"data":{"play":"https://name:pass@example.org/test.mp4"}}'
        ]:
            with self.assertRaises(t.TikTokError):
                t.parse_tikwm(payload)

    def test_fallback_uses_original_short_link_and_preserves_provenance(self):
        source = "https://vt.tiktok.com/ZSbGaWJc9/"
        t._CACHE.clear()
        with TemporaryDirectory() as temp:
            def fake_download(_url, path):
                path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
                return path.stat().st_size

            with patch.object(t.tempfile, "gettempdir", return_value=temp), \
                 patch.object(t, "resolve_short_url", side_effect=t.URLError("blocked")), \
                 patch.object(t, "_yt_download", side_effect=t.TikTokError("blocked")), \
                 patch.object(t, "query_tikwm", return_value=("https://cdn.example.org/v.mp4", {"id": "123"})) as third_party, \
                 patch.object(t, "download_media", side_effect=fake_download), \
                 patch.object(t, "_probe", return_value={"duration_seconds": 19.0}):
                path, info = t.obtain_tiktok(source)
                self.assertTrue(path.exists())
                self.assertEqual(info["requested_url"], source)
                self.assertIsNone(info["resolved_url"])
                self.assertEqual(info["retrieval"]["provider"], "TikWM")
                third_party.assert_called_once_with(source)

    def test_private_network_media_is_rejected(self):
        with TemporaryDirectory() as temp, \
             patch.object(t.socket, "getaddrinfo", return_value=[(None,None,None,None,("127.0.0.1",443))]):
            with self.assertRaises(t.TikTokError):
                t.download_media("https://cdn.example.org/v.mp4", Path(temp) / "clip.mp4")


if __name__ == "__main__":
    unittest.main()

