# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ARG PULSE_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PULSE_VERSION=${PULSE_VERSION} \
    PULSE_DB=/data/pulse.db \
    PULSE_PORT=7860

# Hugging Face Spaces run containers as uid 1000; keep the same user everywhere.
RUN useradd -m -u 1000 pulse && mkdir -p /data && chown pulse:pulse /data
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pulse ./pulse
USER pulse
VOLUME ["/data"]
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/healthz').status==200 else 1)"

CMD ["python", "-m", "pulse"]
