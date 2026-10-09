import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tiktok_auditor as t


class TikTokAuditorTests(unittest.TestCase):
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
