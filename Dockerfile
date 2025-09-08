FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 9999

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9999/api/health || exit 1

CMD ["python3", "-m", "app"]
