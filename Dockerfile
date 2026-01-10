# backend/Dockerfile
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cloud Run sets PORT (usually 8080)
ENV PORT=8080

WORKDIR /app

# (Optional) If you use a DB driver that needs compilation (rare if using psycopg2-binary):
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     build-essential gcc \
#   && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
  && python -m pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the backend code (including app/, wsgi.py, config.py, etc.)
COPY . /app

# Run as non-root (recommended)
RUN adduser --disabled-password --gecos "" appuser \
  && chown -R appuser:appuser /app
USER appuser

# Cloud Run listens on 8080 by default
EXPOSE 8080

# Run with Gunicorn (production server)
CMD ["gunicorn", "--bind", ":8080", "--workers", "2", "--threads", "8", "--timeout", "0", "wsgi:app"]
