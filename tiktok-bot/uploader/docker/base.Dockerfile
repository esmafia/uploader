# Shared base image for api, scheduler, and novnc services.
# Includes Python 3.11 + Node 20 + Playwright Chromium + the tiktok_uploader
# package and its Node signing subprocess deps. Derived images add only their
# own entrypoint and extra dependencies.
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_VERSION=20.x \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# System deps: build toolchain for moviepy/numpy, ffmpeg for video, and the
# runtime libs Playwright-Chromium needs. Keep the apt cache clean.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg git \
        build-essential ffmpeg \
        # Playwright/Chromium runtime libs
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 \
        fonts-liberation fonts-noto-cjk \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION} | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first for better layer caching.
COPY requirements.txt /app/requirements.txt
# fastapi/uvicorn/sqlmodel/etc are installed here so all downstream images have them.
RUN pip install -r /app/requirements.txt \
    && pip install \
        "fastapi>=0.110" "uvicorn[standard]>=0.27" "sqlmodel>=0.0.16" \
        "pydantic>=2.6" "python-multipart>=0.0.9" "httpx>=0.27" \
        "sse-starlette>=2.0" "prompt_toolkit>=3.0" \
        "pytest>=8" "pytest-asyncio>=0.23" "pytest-cov>=5" "freezegun>=1.4"

# Copy the tiktok_uploader package and its Node signing subprocess.
COPY tiktok_uploader /app/tiktok_uploader
COPY config.txt /app/config.txt

# Install Node deps for the signing subprocess (called from tiktok_uploader/tiktok.py:294)
# and pre-fetch Playwright Chromium so services don't try to download at first-run.
WORKDIR /app/tiktok_uploader/tiktok-signature
RUN npm install --omit=dev \
    && npx --yes playwright install chromium --with-deps || true

WORKDIR /app

# Make the package importable regardless of invocation cwd.
ENV PYTHONPATH=/app

# Services append their own COPY + CMD.
