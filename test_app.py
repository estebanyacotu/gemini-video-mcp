import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from mcp.types import CallToolRequest
from google import genai
from google.genai import errors, types

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-a-real-credential")
import app


def file_with_state(state):
    return SimpleNamespace(name="files/test", state=SimpleNamespace(name=state))


class VideoTests(unittest.TestCase):
    def test_file_schema(self):
        tool = asyncio.run(app.mcp.list_tools())[0]
        schema = tool.inputSchema["$defs"]["OpenAIFile"]
        self.assertEqual(set(schema["required"]), {"download_url", "file_id"})
        self.assertEqual(
            set(schema["properties"]),
            {"download_url", "file_id", "mime_type", "file_name"},
        )
        self.assertEqual(tool.meta["openai/fileParams"], ["video"])

    def test_youtube_variants(self):
        video_id = "YBPHvu1PVAc"
        expected = "https://www.youtube.com/watch?v=" + video_id
        for url in [
            "https://youtu.be/" + video_id + "?si=test",
            "https://www.youtube.com/live/" + video_id + "?feature=share",
            "https://m.youtube.com/shorts/" + video_id,
            expected + "&t=10",
        ]:
            with self.subTest(url=url):
                self.assertEqual(app.normalize_youtube_url(url), expected)

    def test_reject_invalid_urls(self):
        for url in [
            "https://youtube.com.evil.example/watch?v=YBPHvu1PVAc",
            "[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=)YBPHvu1PVAc",
            "https://www.youtube.com/watch?v=missing",
            "https://example.com/youtu.be/YBPHvu1PVAc",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                app.normalize_youtube_url(url)

    def test_file_processing(self):
        sdk = Mock()
        sdk.files.get.return_value = file_with_state("ACTIVE")
        with patch.object(app, "client", sdk), patch.object(app.time, "sleep"):
            self.assertEqual(
                app.wait_until_active(file_with_state("PROCESSING")).state.name,
                "ACTIVE",
            )
            with self.assertRaises(RuntimeError):
                app.wait_until_active(file_with_state("FAILED"))
            with self.assertRaises(TimeoutError):
                app.wait_until_active(file_with_state("PROCESSING"), timeout_seconds=0)

    def test_attachment_success_and_cleanup(self):
        sdk = Mock()
        sdk.files.upload.return_value = file_with_state("ACTIVE")
        sdk.models.generate_content.return_value = SimpleNamespace(text="Análisis de prueba")
        with patch.object(app, "client", sdk), patch.object(app, "download_openai_file"):
            result = app.analizar_video(video={
                "download_url": "https://example.com/authorized.mp4",
                "file_id": "file_test",
            })
        self.assertEqual(result, "Análisis de prueba")
        self.assertFalse(os.path.exists(sdk.files.upload.call_args.kwargs["file"]))
        sdk.files.delete.assert_called_once_with(name="files/test")

    def test_503_is_error_and_cleans_up(self):
        sdk = Mock()
        sdk.files.upload.return_value = file_with_state("ACTIVE")
        sdk.models.generate_content.side_effect = errors.ServerError(
            503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}}
        )
        with patch.object(app, "client", sdk), patch.object(app, "download_openai_file"):
            with self.assertRaisesRegex(RuntimeError, "tras los reintentos"):
                app.analizar_video(video={
                    "download_url": "https://example.com/authorized.mp4",
                    "file_id": "file_test",
                })
        self.assertFalse(os.path.exists(sdk.files.upload.call_args.kwargs["file"]))
        sdk.files.delete.assert_called_once_with(name="files/test")

    def test_sdk_retries_503(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) < 3:
                return httpx.Response(503, json={"error": {
                    "code": 503, "message": "high demand", "status": "UNAVAILABLE"
                }})
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": "ok"}]},
                "finishReason": "STOP",
            }]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            sdk = genai.Client(api_key="test-key", http_options=types.HttpOptions(
                httpx_client=http_client,
                retry_options=types.HttpRetryOptions(
                    attempts=4, initial_delay=0.001, max_delay=0.002,
                    jitter=0, http_status_codes=[503],
                ),
            ))
            try:
                result = sdk.models.generate_content(model=app.MODEL, contents="test")
                self.assertEqual(result.text, "ok")
                self.assertEqual(len(requests), 3)
            finally:
                sdk.close()

    def test_youtube_analysis(self):
        sdk = Mock()
        sdk.interactions.create.return_value = SimpleNamespace(output_text="Análisis de YouTube")
        with patch.object(app, "client", sdk):
            result = app.analizar_video(url="https://youtu.be/YBPHvu1PVAc")
        self.assertEqual(result, "Análisis de YouTube")
        inputs = sdk.interactions.create.call_args.kwargs["input"]
        self.assertEqual(inputs[1]["uri"], "https://www.youtube.com/watch?v=YBPHvu1PVAc")

    def test_missing_or_ambiguous_input(self):
        with self.assertRaises(ValueError):
            app.analizar_video()
        with self.assertRaises(ValueError):
            app.analizar_video(video={"download_url": "https://example.com/v.mp4", "file_id": "x"},
                               url="https://youtu.be/YBPHvu1PVAc")

    def test_mcp_marks_failures_as_errors(self):
        async def call():
            handler = app.mcp._mcp_server.request_handlers[CallToolRequest]
            return await handler(CallToolRequest(
                method="tools/call", params={"name": "analizar_video", "arguments": {}}
            ))
        result = asyncio.run(call())
        self.assertTrue(result.root.isError)

    def test_sdk_limits_retries_and_does_not_retry_400(self):
        for code, expected_attempts in [(503, 4), (400, 1)]:
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(code, json={"error": {
                    "code": code, "message": "test error"
                }})

            with self.subTest(code=code), httpx.Client(
                transport=httpx.MockTransport(handler)
            ) as http_client:
                retries = app.client._api_client._http_options.retry_options.model_copy(
                    update={"initial_delay": 0.001, "max_delay": 0.002, "jitter": 0}
                )
                sdk = genai.Client(api_key="test-key", http_options=types.HttpOptions(
                    httpx_client=http_client, retry_options=retries,
                ))
                try:
                    with self.assertRaises(errors.APIError):
                        sdk.models.generate_content(model=app.MODEL, contents="test")
                    self.assertEqual(len(requests), expected_attempts)
                finally:
                    sdk.close()


if __name__ == "__main__":
    unittest.main()
