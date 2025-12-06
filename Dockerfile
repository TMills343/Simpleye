# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-traditional \
    ffmpeg \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY src/backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Healthcheck: rely on /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys;\
    import os; url=f'http://localhost:{os.environ.get("APP_PORT","8000")}/health';\
    urllib.request.urlopen(url).read(); print('ok')" || exit 1

# Default to gunicorn in production; point to the correct app factory
ENV APP_PORT=8000
# Use the package path where create_app() is defined
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--timeout", "0", "--graceful-timeout", "0", "src.backend.app:create_app()"]
