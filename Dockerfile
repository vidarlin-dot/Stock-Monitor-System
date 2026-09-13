# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

# Install system deps for playwright chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-noto-cjk \
    curl \
    && curl -sL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ src/
COPY cache/ cache/
COPY data/ data/

# Pre-install playwright browsers
RUN playwright install chromium

EXPOSE 5000

ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=src.line_bot_webhook

CMD ["python", "-m", "src.line_bot_webhook"]
