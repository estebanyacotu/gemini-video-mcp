"""Bounded attachment transport. Signed URLs are never logged or persisted."""
import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

MAX_BYTES = 200 * 1024 * 1024


def validate_download_url(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    # Host-provided attachment storage; reject arbitrary fetch targets and redirects.
    allowed = host.endswith('.oaiusercontent.com') or (
        host.startswith(('oaisdmntpr', 'oaisdsorpr', 'sdmntpr')) and (
            host.endswith('.blob.core.windows.net') or host.endswith('.amazonaws.com')))
    if p.scheme != 'https' or not allowed or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Se necesita un adjunto con URL HTTPS autorizada por ChatGPT.')
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Destino del adjunto no permitido.')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_openai_file(url, path):
    validate_download_url(url)
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(url, timeout=60) as response, open(path, 'wb') as out:
        if int(response.headers.get('Content-Length', 0)) > MAX_BYTES:
            raise ValueError('El clip supera 200 MiB; exporta una versión más ligera.')
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_BYTES:
                raise ValueError('El clip supera 200 MiB.')
            out.write(chunk)
        if not count:
            raise ValueError('El adjunto está vacío.')
