FROM python:3.11-slim

WORKDIR /app
RUN useradd --create-home --uid 1000 c2

COPY packages/mediguard-common /tmp/mediguard-common
COPY components/c2-intent-classifier /app

RUN pip install --no-cache-dir -e /app \
    && mkdir -p /data /data/chroma \
    && chown -R c2:c2 /app /data

USER c2
ENV MG_OUTBOX_PATH=/data/c2.db \
    C2_CHROMA_DIR=/data/chroma \
    C2_CLASSIFIER_CHECKPOINT_PATH=/app/models/classifier \
    C2_PORT=8002

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health')"

CMD ["python", "-m", "c2.main"]
