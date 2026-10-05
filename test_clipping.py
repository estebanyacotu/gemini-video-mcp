import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
os.environ.setdefault('GEMINI_API_KEY', 'test-key-not-a-real-credential')
import app
from clipping import windows, coverage, parse_srt, parse_json, BlockAnalysis, validate_block
from video_io import validate_download_url

URL = 'https://youtu.be/YBPHvu1PVAc'

def result():
    return {'audio_observed': True, 'video_observed': True, 'observation_limits': [], 'transcript': [], 'candidates': [{
        'title': 'Hallazgo', 'segments': [{'start': 2, 'end': 8, 'quote': 'una frase real'}],
        'rationale': 'Cierra una idea', 'visual_evidence': 'Persona habla', 'audio_evidence': 'Voz clara',
        'hook_overlay': 'Mira esto', 'caption': 'Descripción', 'cover': 'Portada', 'edit_notes': 'Recorte', 'editorial_score': 75}]}

class ClippingTests(unittest.TestCase):
    def test_long_live_has_no_holes(self):
        for duration in (1, 480, 481, 7213):
            blocks = windows(duration)
            report = coverage(duration, [{**b, 'status': 'reviewed'} for b in blocks])
            self.assertTrue(report['complete'])
            self.assertEqual(report['reviewed_seconds'], duration)

    def test_missing_block_not_complete(self):
        blocks = windows(1500)
        self.assertFalse(coverage(1500, [{**b, 'status': 'reviewed'} for b in blocks if b['block'] != 2])['complete'])

    def test_bad_duration(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError): windows(value)

    def test_parse_srt_preserves_absolute_times(self):
        srt = '1\n00:10:02,250 --> 00:10:08,500\n<b>una frase</b> real\n\n'
        self.assertEqual(parse_srt(srt), [{'start': 602.25, 'end': 608.5, 'quote': 'una frase real'}])

    def test_out_of_window_rejected(self):
        with self.assertRaises(ValueError): validate_block(parse_json(json.dumps(result()), BlockAnalysis), 480, 960, [])

    def test_quote_evidence_is_checked(self):
        parsed = parse_json(json.dumps(result()), BlockAnalysis)
        sub = [{'start': 2, 'end': 8, 'quote': 'Una frase real.'}]
        self.assertEqual(validate_block(parsed, 0, 10, sub)[0]['quote_evidence'], 'subtitle_match')
        sub[0]['quote'] = 'otra frase'
        self.assertEqual(validate_block(parsed, 0, 10, sub)[0]['quote_evidence'], 'model_audio_unverified')

    def test_wrong_json_rejected(self):
        for raw in ('no json', '{}', '{"candidates": []}'):
            with self.assertRaises(ValueError): parse_json(raw, BlockAnalysis)

    def test_live_cannot_be_treated_as_complete_recording(self):
        with patch.object(app.transcriptor, '_info', return_value={'duration': 500, 'is_live': True}):
            with self.assertRaises(ValueError): app.preparar_live(URL)

    def test_fallback_transcription_is_explicit(self):
        sdk = Mock()
        sdk.interactions.create.return_value = SimpleNamespace(output_text=json.dumps(result()))
        with patch.object(app, 'client', sdk), patch.object(app.transcriptor, '_info', return_value={'duration': 700}), patch.object(app.transcriptor, '_subtitle_payload', side_effect=RuntimeError('429')):
            data = app.analizar_bloque_live(URL, 1)
        self.assertEqual(data['transcript_quality'], 'model_estimated')
        self.assertIsNone(data['revenue'])
        self.assertTrue(data['observation_limits'])
        args = sdk.interactions.create.call_args.kwargs
        self.assertFalse(args['store'])
        self.assertEqual(args['input'][0]['processing'], {'type': 'static', 'start_offset': '0s', 'end_offset': '480s', 'fps': 1})

    def test_missing_audio_is_partial(self):
        value = result(); value['audio_observed'] = False
        with patch.object(app, 'infer', return_value=json.dumps(value)), patch.object(app.transcriptor, '_info', return_value={'duration': 700}), patch.object(app.transcriptor, '_subtitle_payload', side_effect=ValueError('none')):
            self.assertEqual(app.analizar_bloque_live(URL, 1)['status'], 'partial')

    def test_real_sdk_accepts_static_video_payload(self):
        import httpx
        from google import genai
        from google.genai import types
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 'test', 'status': 'completed',
                'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': 'ok'}]}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            sdk = genai.Client(api_key='test-key', http_options=types.HttpOptions(httpx_client=http_client))
            try:
                with patch.object(app, 'client', sdk):
                    output = app.infer({'type': 'video', 'uri': URL, 'processing': {
                        'type': 'static', 'start_offset': '0s', 'end_offset': '480s', 'fps': 1}}, 'test')
                self.assertEqual(output, 'ok')
                self.assertEqual(len(seen), 1)
                self.assertFalse(seen[0]['store'])
                self.assertEqual(seen[0]['input'][0]['content'][0]['processing']['end_offset'], '480s')
            finally:
                sdk.close()

    def test_mobile_file_contract(self):
        tools = {t.name: t for t in asyncio.run(app.mcp.list_tools())}
        t = tools['evaluar_clip']
        self.assertEqual(t.meta['openai/fileParams'], ['video'])
        schema = t.inputSchema['$defs']['OpenAIFile']
        self.assertEqual(set(schema['required']), {'file_id','download_url'})
        self.assertEqual(len(schema['properties']), 4)
        self.assertTrue({'preparar_live','analizar_bloque_live'}.issubset(tools))

    def test_untrusted_download_hosts_rejected(self):
        for url in ('http://127.0.0.1/x', 'https://example.com/x', 'https://sdmntpr.evil.test/x', 'file:///etc/passwd'):
            with self.assertRaises(ValueError): validate_download_url(url)

    def test_failed_processing_still_deletes_upload(self):
        sdk = Mock()
        sdk.files.upload.return_value = SimpleNamespace(name='files/test')
        with patch.object(app, 'client', sdk), patch.object(app, 'download_openai_file'), patch.object(app, 'wait_until_active', side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError): app.analyze_attachment({'download_url':'redacted','file_id':'test'}, 'test')
        sdk.files.delete.assert_called_once_with(name='files/test')
        self.assertFalse(os.path.exists(sdk.files.upload.call_args.kwargs['file']))

if __name__ == '__main__': unittest.main()
