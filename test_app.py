import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('GEMINI_API_KEY', 'test-key')
import app
import transcriptor
from starlette.testclient import TestClient


class RepairTests(unittest.TestCase):
    def test_http_initialize_and_health(self):
        with TestClient(app.application) as client:
            self.assertEqual(client.get('/health').json()['status'], 'ok')
            result = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': '2025-03-26', 'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '1'},
                }})
            self.assertEqual(result.status_code, 200)
            self.assertIn('serverInfo', result.json()['result'])
        self.assertIn('/sse', [r.path for r in app.application.routes])

    def test_youtube_uses_video_part(self):
        sdk = Mock()
        sdk.models.generate_content.return_value = SimpleNamespace(text='observed content')
        with patch.object(app, 'client', sdk):
            self.assertEqual(app.analizar_video(url='https://youtu.be/v3-odNpotVc'), 'observed content')
        parts = sdk.models.generate_content.call_args.kwargs['contents']
        self.assertEqual(parts[0].file_data.file_uri, 'https://www.youtube.com/watch?v=v3-odNpotVc')

    def test_upload_cleanup_on_failure(self):
        sdk = Mock()
        sdk.files.upload.return_value = SimpleNamespace(name='files/test', state=SimpleNamespace(name='ACTIVE'))
        sdk.models.generate_content.side_effect = RuntimeError('provider unavailable')
        with patch.object(app, 'client', sdk), patch.object(app, 'download_openai_file'):
            with self.assertRaisesRegex(RuntimeError, 'provider unavailable'):
                app.analizar_video(video={'download_url': 'https://example.com/a.mp4', 'file_id': 'test'})
        self.assertFalse(os.path.exists(sdk.files.upload.call_args.kwargs['file']))
        sdk.files.delete.assert_called_once_with(name='files/test')

    def test_live_chat_is_not_subtitles(self):
        with self.assertRaisesRegex(ValueError, 'Subtitle track unavailable'):
            transcriptor._choose_track({'subtitles': {'live_chat': [{'ext': 'json'}]}}, None, None, 'srt')

    def test_fallback_only_for_server_errors(self):
        for code in [503, 400]:
            sdk=Mock()
            sdk.models.generate_content.side_effect=[app.errors.APIError(code, {'error': {'code': code, 'message': 'test'}}), SimpleNamespace(text='audio result')]
            with self.subTest(code=code), patch.object(app, 'client', sdk):
                if code == 503:
                    result=app.generate_video(['test'])
                    self.assertIn(app.FALLBACK_MODEL, result)
                    self.assertEqual(sdk.models.generate_content.call_count, 2)
                else:
                    with self.assertRaises(app.errors.APIError): app.generate_video(['test'])
                    self.assertEqual(sdk.models.generate_content.call_count, 1)

    def test_missing_key_reports_real_error(self):
        with patch.object(app, 'client', None), self.assertRaisesRegex(RuntimeError, 'GEMINI_API_KEY'):
            app.analizar_video(url='https://youtu.be/v3-odNpotVc')

if __name__ == '__main__':
    unittest.main()
