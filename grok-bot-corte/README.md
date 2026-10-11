# Grok Bot Corte

Tablero de cortes con extracción de transcripción (yt-dlp). App independiente dentro de esta carpeta.

- Local: `ACCESS_TOKEN=xxxx python3 server.py` (requiere `pip install yt-dlp`).
- Render: Web Service Docker, contexto `grok-bot-corte`, Dockerfile `grok-bot-corte/Dockerfile`, variable `ACCESS_TOKEN`.
- En la app: Lives > Servidor > pega el token. La URL base puede quedar vacía.
- YouTube puede bloquear IPs de datacenter.
