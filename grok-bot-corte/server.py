#!/usr/bin/env python3
"""Servidor mínimo: sirve la app estática y GET /api/transcript (yt-dlp). Sin Flask."""
import hmac, json, os, re, sys, tempfile, threading, shutil
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get('CACHE_DIR', os.path.join(HERE, 'cache'))
TOKEN = os.environ.get('ACCESS_TOKEN', '')
UPSTREAM = os.environ.get('TRANSCRIPT_UPSTREAM_MCP', 'https://gemini-video-mcp-4ev0.onrender.com/mcp')  # vacío = desactivado
SOURCE_MODE = os.environ.get('TRANSCRIPT_SOURCE', 'auto')  # auto = yt-dlp local y, si falla, upstream; upstream = solo upstream
PREF_LANGS = [x for x in os.environ.get('TRANSCRIPT_PREF_LANGS', 'es,pt,en').split(',') if x]
STATIC = {'index.html', 'app.js', 'style.css', 'example.js', 'manifest.json', 'icon.svg', 'config.js'}
MIME = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css', 'json': 'application/json', 'svg': 'image/svg+xml'}
LOCKS, LL = {}, threading.Lock()

class Err(Exception):
    def __init__(s, status, code, msg): s.status, s.code, s.msg = status, code, msg

def video_id(u):
    u = (u or '').strip()
    if re.fullmatch(r'[\w-]{11}', u): return u
    p = urlparse(u if '//' in u else 'https://' + u)
    host = (p.hostname or '').lower().replace('www.', '').replace('m.', '')
    if host == 'youtu.be': c = p.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'music.youtube.com'):
        m = re.match(r'/(live|shorts|embed|v)/([\w-]{11})', p.path)
        c = m.group(2) if m else (parse_qs(p.query).get('v') or [''])[0]
    else: c = ''
    if not re.fullmatch(r'[\w-]{11}', c or ''): raise Err(400, 'url_invalida', 'El enlace no parece ser de un video o live de YouTube.')
    return c

def ts(s):
    p = s.replace(',', '.').split(':'); p = [float(x) for x in p]
    while len(p) < 3: p.insert(0, 0)
    return p[0] * 3600 + p[1] * 60 + p[2]

def parse_vtt(txt):
    """Devuelve [(inicio, palabra/frase, fin_cue)]. Auto-captions 'rolling': solo la última línea de cada cue,
    con tiempos por palabra; se descartan cues de relevo (<50 ms) y repeticiones idénticas. Texto literal."""
    words, last = [], None
    rolling = bool(re.search(r'<\d+:\d+:\d+\.\d+><c', txt))
    for b in re.split(r'\n\n+', txt):
        m = re.search(r'(\d+:\d+:\d+[.,]\d+) --> (\d+:\d+:\d+[.,]\d+)', b)
        if not m: continue
        a, e = ts(m.group(1)), ts(m.group(2))
        if e - a < 0.05: continue
        lines = [l for l in b.split('\n')[1:] if l.strip()]
        if not lines: continue
        if rolling:  # auto con tiempos por palabra
            raw = lines[-1]
            clean = re.sub(r'<[^>]+>', '', raw).strip()
            if words and clean == last: continue
            last = clean; t = a
            for p in re.split(r'(<\d+:\d+:\d+\.\d+>)', raw):
                mm = re.match(r'<(\d+:\d+:\d+\.\d+)>', p)
                if mm: t = ts(mm.group(1)); continue
                for w in re.sub(r'</?c[^>]*>', '', p).split(): words.append((round(t, 2), w, e))
        else:  # oficial / sin tiempos por palabra: cue completo, repartido en el rango del cue
            clean = ' '.join(re.sub(r'<[^>]+>', '', l).strip() for l in lines).strip()
            if not clean or clean == last: continue
            last = clean; ws = clean.split(); step = (e - a) / len(ws)
            for i, w in enumerate(ws): words.append((round(a + i * step, 2), w, e))
    return words

def build_segments(words, gap_s=8.0):
    segs, cur = [], []
    def flush(nxt_start=None):
        nonlocal cur
        if not cur: return
        end = min(nxt_start, cur[-1][2]) if nxt_start is not None else cur[-1][2]
        if end <= cur[0][0]: end = cur[-1][0] + 0.5
        segs.append({'start_sec': cur[0][0], 'end_sec': round(end, 2), 'text': ' '.join(w[1] for w in cur)}); cur = []
    for i, w in enumerate(words):
        if cur:
            gap = w[0] - cur[-1][0] > gap_s
            brk = gap or len(cur) >= 10 or (len(cur) >= 5 and re.search(r'[.?!…]["”»)]?$', cur[-1][1])) or (w[0] - cur[0][0] > 6)
            if brk:
                # fin del segmento: el siguiente arranca en w
                flush(w[0] if not gap else None)
                if gap:
                    s = segs[-1]['end_sec']
                    segs.append({'start_sec': min(cur_last + 1, w[0]), 'end_sec': w[0], 'text': f'[~{int(w[0]-cur_last)} s sin transcripción automática]', 'gap': True})
        cur.append(w); cur_last = w[0]
    flush()
    return segs


def parse_srt(txt):
    """SRT (rolling de auto-captions) -> [(inicio, palabra, fin_cue)]. Quita líneas ya emitidas en el cue anterior; no altera palabras."""
    words, prev = [], set()
    for b in re.split(r'\n\s*\n', txt.replace('\r\n', '\n').strip()):
        ls = b.split('\n')
        i = next((k for k, l in enumerate(ls) if '-->' in l), None)
        if i is None: continue
        m = re.search(r'(\d+:\d+:\d+[.,]\d+)\s*-->\s*(\d+:\d+:\d+[.,]\d+)', ls[i])
        if not m: continue
        a, e = ts(m.group(1)), ts(m.group(2))
        lines = [re.sub(r'<[^>]+>', '', l).strip() for l in ls[i + 1:]]
        lines = [l for l in lines if l]
        new = [l for l in lines if l not in prev]
        prev = set(lines)
        if not new: continue
        ws = ' '.join(new).split(); dur = min(max(e - a, 0.5), 0.5 * len(ws)); step = dur / len(ws)
        for k, w in enumerate(ws): words.append((round(a + k * step, 2), w, e))
    return words

def mcp_call(name, args, timeout=150):
    import urllib.request
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}).encode()
    rq = urllib.request.Request(UPSTREAM, body, {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-03-26'})
    try:
        raw = urllib.request.urlopen(rq, timeout=timeout).read().decode('utf-8')
    except Exception as ex:
        raise Err(502, 'upstream_error', 'No se pudo contactar el servicio de transcripción externo: ' + str(ex)[:160])
    msg = None
    if raw.lstrip().startswith('{'): msg = json.loads(raw)
    else:
        for l in raw.splitlines():
            if l.startswith('data:'):
                try: msg = json.loads(l[5:].strip()); 
                except Exception: pass
    if not msg or 'result' not in msg: raise Err(502, 'upstream_error', 'Respuesta inesperada del servicio externo: ' + str((msg or {}).get('error') or raw[:120]))
    r = msg['result']; txt = ''.join(c.get('text', '') for c in r.get('content', []) if c.get('type') == 'text')
    if r.get('isError'): raise Err(502, 'upstream_sin_subtitulos', 'El servicio externo no pudo obtener subtítulos: ' + txt[:220])
    sc = r.get('structuredContent')
    if sc is None:
        try: sc = json.loads(txt)
        except Exception: raise Err(502, 'upstream_error', 'Respuesta no interpretable del servicio externo.')
    return sc

def fetch_upstream(vid):
    url = 'https://www.youtube.com/watch?v=' + vid
    try: info = mcp_call('get_video_info', {'url': url}, 60)
    except Err: info = {}
    av = mcp_call('get_available_subtitles', {'url': url}, 90)
    official, auto = av.get('official') or [], av.get('auto') or []
    def code(x): return x if isinstance(x, str) else (x.get('lang') or x.get('code') or '')
    official, auto = [code(x) for x in official], [code(x) for x in auto]
    orig = [k for k in auto if k.endswith('-orig')]
    if official:
        kind, lang = 'oficial', next((l for l in PREF_LANGS if l in official), official[0])
    elif orig:
        pick = next((l + '-orig' for l in PREF_LANGS if l + '-orig' in orig), None)
        if not pick: raise Err(502, 'upstream_idioma_ambiguo', 'El servicio externo lista pistas originales (' + ', '.join(orig[:5]) + ') pero ninguna está entre los idiomas preferidos; no se eligió ninguna para evitar traducciones.')
        kind, lang = 'automatico', pick
    else:
        raise Err(502, 'upstream_sin_subtitulos', 'El servicio externo tampoco ve subtítulos para este video ahora mismo (puede estar bloqueado por YouTube). No se genera texto inventado.')
    chunks, cursor = [], None
    for _ in range(60):
        a = {'url': url, 'type': 'official' if kind == 'oficial' else 'auto', 'lang': lang, 'format': 'srt', 'response_limit': 200000}
        if cursor: a['next_cursor'] = cursor
        sc = mcp_call('get_raw_subtitles', a)
        chunks.append(sc.get('content') or '')
        if not sc.get('is_truncated'): break
        cursor = sc.get('next_cursor')
        if not cursor: raise Err(502, 'upstream_truncado', 'El servicio externo devolvió la transcripción incompleta y sin cursor para continuar.')
    else: raise Err(502, 'upstream_truncado', 'Demasiadas páginas del servicio externo.')
    words = parse_srt(''.join(chunks))
    if not words: raise Err(502, 'upstream_sin_subtitulos', 'El subtítulo recibido del servicio externo está vacío.')
    segs = build_segments(words)
    dur = info.get('duration') or None
    return {'video': {'id': vid, 'title': info.get('title'), 'channel': info.get('channel') or info.get('uploader'), 'duration': dur,
                      'is_live': bool(info.get('isLive')), 'was_live': bool(info.get('wasLive')), 'url': url},
            'track': {'type': kind, 'lang': lang}, 'segments': segs, 'cached': False, 'source': 'upstream_mcp'}

def fetch(vid):
    try: return fetch_cached(vid)
    except Err as e:
        if SOURCE_MODE != 'upstream' and UPSTREAM and e.code in ('youtube_error', 'descarga_fallida'):
            try: d = fetch_upstream(vid)
            except Err as e2: raise Err(502, e2.code, e2.msg + ' (YouTube bloqueó la consulta directa: ' + e.msg[:90] + ')')
            save_cache(vid, d); return d
        raise

def save_cache(vid, d):
    if not d['video'].get('is_live'):
        os.makedirs(CACHE, exist_ok=True); json.dump(d, open(os.path.join(CACHE, vid + '.json'), 'w'), ensure_ascii=False)

def fetch_cached(vid):
    path = os.path.join(CACHE, vid + '.json')
    if os.path.exists(path):
        d = json.load(open(path)); d['cached'] = True; d.setdefault('source', 'local_ytdlp'); return d
    with LL: lock = LOCKS.setdefault(vid, threading.Lock())
    with lock:
        if os.path.exists(path):
            d = json.load(open(path)); d['cached'] = True; d.setdefault('source', 'local_ytdlp'); return d
        if SOURCE_MODE == 'upstream':
            if not UPSTREAM: raise Err(502, 'upstream_error', 'Servicio externo no configurado.')
            d = fetch_upstream(vid); save_cache(vid, d); return d
        import yt_dlp
        url = 'https://www.youtube.com/watch?v=' + vid
        tmp = tempfile.mkdtemp(prefix='yt_')
        try:
            base = {'quiet': True, 'no_warnings': True, 'skip_download': True}
            try:
                with yt_dlp.YoutubeDL(base) as y: info = y.extract_info(url, download=False)
            except Exception as ex:
                m = str(ex)
                if re.search(r'private|unavailable|not available|removed|terminated', m, re.I): raise Err(404, 'video_no_disponible', 'El video no está disponible o es privado.')
                raise Err(502, 'youtube_error', 'No se pudo consultar YouTube: ' + m[:200])
            subs, auto = info.get('subtitles') or {}, info.get('automatic_captions') or {}
            subs = {k: v for k, v in subs.items() if k != 'live_chat'}
            def _is_orig(k):  # '<lang>-orig' real: ninguna URL de la pista es traducción (tlang=)
                fs = auto.get(k) or []
                return k.endswith('-orig') and fs and not any('tlang=' in (f.get('url') or '') for f in fs)
            orig = [k for k in auto if _is_orig(k)]
            pl = (info.get('language') or '').split('-')[0]
            orig.sort(key=lambda k: 0 if k.split('-')[0] == pl else 1)
            if subs:
                lang = info.get('language') if info.get('language') in subs else next(iter(subs)); kind = 'oficial'
            elif orig: lang, kind = orig[0], 'automatico'
            else:
                if info.get('is_live'): raise Err(409, 'live_en_curso', 'El live sigue en curso y aún no tiene subtítulos. Vuelve a intentar cuando termine.')
                raise Err(404, 'sin_subtitulos', 'Este video no tiene subtítulos oficiales ni automáticos en idioma original; no se genera texto inventado.')
            opts = dict(base, writesubtitles=kind == 'oficial', writeautomaticsub=kind == 'automatico', subtitleslangs=[lang], subtitlesformat='vtt', outtmpl=os.path.join(tmp, '%(id)s'))
            try:
                with yt_dlp.YoutubeDL(opts) as y: y.extract_info(url, download=True)
            except Exception as ex: raise Err(502, 'descarga_fallida', 'No se pudo descargar el subtítulo: ' + str(ex)[:200])
            f = next((x for x in os.listdir(tmp) if x.endswith('.vtt')), None)
            if not f: raise Err(502, 'descarga_fallida', 'YouTube no devolvió el archivo de subtítulos.')
            words = parse_vtt(open(os.path.join(tmp, f), encoding='utf-8').read())
            if not words: raise Err(404, 'sin_subtitulos', 'El subtítulo descargado está vacío.')
            segs = build_segments(words)
            d = {'video': {'id': vid, 'title': info.get('title'), 'channel': info.get('channel') or info.get('uploader'), 'duration': info.get('duration'),
                           'is_live': bool(info.get('is_live')), 'was_live': bool(info.get('was_live')), 'url': 'https://www.youtube.com/watch?v=' + vid},
                 'track': {'type': kind, 'lang': lang}, 'segments': segs, 'cached': False, 'source': 'local_ytdlp'}
            if not info.get('is_live'):
                os.makedirs(CACHE, exist_ok=True); json.dump(d, open(path, 'w'), ensure_ascii=False)
            return d
        finally: shutil.rmtree(tmp, ignore_errors=True)

class H(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def cors(s):
        s.send_header('Access-Control-Allow-Origin', '*'); s.send_header('Access-Control-Allow-Headers', 'X-Access-Token'); s.send_header('Access-Control-Allow-Methods', 'GET,OPTIONS')
    def send(s, st, body, ctype='application/json; charset=utf-8'):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        s.send_response(st); s.send_header('Content-Type', ctype); s.send_header('Content-Length', str(len(b))); s.send_header('Cache-Control', 'no-store'); s.cors(); s.end_headers(); s.wfile.write(b)
    def do_OPTIONS(s): s.send(204, b'')
    def do_GET(s):
        u = urlparse(s.path); q = parse_qs(u.query)
        if u.path == '/api/transcript':
            tok = s.headers.get('X-Access-Token') or (q.get('token') or [''])[0]
            if not TOKEN or not hmac.compare_digest(tok.encode(), TOKEN.encode()):
                return s.send(401, {'error': 'no_autorizado', 'message': 'Token de acceso faltante o incorrecto.'})
            try: return s.send(200, fetch(video_id((q.get('url') or [''])[0])))
            except Err as e: return s.send(e.status, {'error': e.code, 'message': e.msg})
            except Exception as e: return s.send(500, {'error': 'error_interno', 'message': str(e)[:200]})
        if u.path == '/api/health': return s.send(200, {'ok': True})
        name = 'index.html' if u.path in ('/', '') else u.path.lstrip('/')
        if name in STATIC and os.path.exists(os.path.join(HERE, name)):
            return s.send(200, open(os.path.join(HERE, name), 'rb').read(), MIME.get(name.rsplit('.', 1)[-1], 'text/plain'))
        s.send(404, {'error': 'no_encontrado'})

if __name__ == '__main__':
    if not TOKEN: print('ERROR: define ACCESS_TOKEN', file=sys.stderr); sys.exit(1)
    port = int(os.environ.get('PORT', '8080'))
    print('Escuchando en', port, flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()
