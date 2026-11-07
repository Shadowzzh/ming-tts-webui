# syntax=docker/dockerfile:1
# Both source checkouts and prebuilt release bundles use the same runtime.
ARG WEB_ASSETS=frontend

FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM scratch AS bundled
COPY static/ /frontend/dist/

FROM ${WEB_ASSETS} AS web-assets

FROM python:3.12-slim-bookworm AS inference
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MING_REPO_DIR=/opt/ming \
    MING_MODEL_PATH=/models/Ming-3.0-tts-0.5B \
    MING_DEVICE=cuda:0 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Pinned upstream inference code, independent of this application's parent path.
ADD --checksum=sha256:1ad012609a3d3e23a6d79befa008c640c1ac8f17ac0a632f41a2a9c9c11259fa \
    https://codeload.github.com/inclusionAI/Ming-3.0-tts/tar.gz/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54 /tmp/ming.tar.gz
RUN mkdir -p /opt/ming \
    && tar -xzf /tmp/ming.tar.gz --strip-components=1 -C /opt/ming \
    && rm /tmp/ming.tar.gz
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 60 --retries 3 --index-url https://download.pytorch.org/whl/cu124 \
    torch==2.6.0 torchaudio==2.6.0
COPY requirements-model.txt requirements-web.txt /tmp/
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 60 --retries 3 -r /tmp/requirements-model.txt -r /tmp/requirements-web.txt \
    && python -m pip check \
    && PYTHONPATH=/opt/ming python -c "from ming_tts import MingTTS; import torch, torchaudio"

FROM inference AS app
ENV MING_PORT=17863 MING_BIND=0.0.0.0
WORKDIR /app
COPY app/ /app/app/
COPY --from=web-assets /frontend/dist/ /app/static/
COPY LICENSE /app/LICENSE
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin studio \
    && mkdir -p /models /app/uploads /app/outputs /app/state \
    && chown -R studio:studio /app /models
USER studio
EXPOSE 17863
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:17863/api/status', timeout=8)"
ENTRYPOINT ["python", "-u", "-m", "app.server"]
