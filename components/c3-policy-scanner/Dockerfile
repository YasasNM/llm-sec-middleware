# C3 Policy Scanner. Build context is the component folder:
#   docker build -t mediguard/c3 components/c3-policy-scanner
# One worker only: one file watcher, one index, one model copy (blueprint 19.4).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# CPU-only torch keeps the image small. Skip with --build-arg EMBEDDER=hashing for a light image.
ARG EMBEDDER=sentence_transformers
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip \
 && if [ "$EMBEDDER" = "sentence_transformers" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu ".[models]"; \
    else pip install .; fi

COPY configs ./configs
COPY models ./models

ENV C3_CONFIG_FILE=/app/configs/default.yaml \
    C3_SEVERITY_FILE=/app/configs/severity.yaml \
    C3_DB_PATH=/data/c3.db \
    C3_CHROMA_DIR=/data/chroma \
    C3_POLICY_DIR=/data/policies \
    C3_WATCH_DIR=/data/policies_watch \
    C3_MODEL_DIR=/app/models

RUN useradd --create-home --uid 10001 c3 && mkdir -p /data && chown -R c3 /data
USER c3
VOLUME ["/data"]
EXPOSE 8003
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8003/health',timeout=2)"

CMD ["uvicorn", "c3.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
