# Build from the repository root:  docker build -f components/c4-risk-profiler/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY components/c4-risk-profiler /app/components/c4-risk-profiler
COPY contracts /app/contracts
RUN pip install --no-cache-dir -e /app/components/c4-risk-profiler
ENV C4_RULES_FILE=/app/components/c4-risk-profiler/configs/rules.yaml \
    C4_CONTRACTS_DIR=/app/contracts
RUN useradd --create-home c4 && chown -R c4 /app
USER c4
EXPOSE 8004
CMD ["uvicorn", "c4.main:app", "--host", "0.0.0.0", "--port", "8004"]
