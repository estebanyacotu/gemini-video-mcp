FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html app.js style.css example.js manifest.json icon.svg config.js ./
ENV PORT=8080 CACHE_DIR=/tmp/cache
EXPOSE 8080
CMD ["python", "server.py"]
