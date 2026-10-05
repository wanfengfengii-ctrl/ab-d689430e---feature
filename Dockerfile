FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY scripts ./scripts

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --access-logfile - app.server:app"]
