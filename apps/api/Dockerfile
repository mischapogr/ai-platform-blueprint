FROM python:3.12-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.txt \
    && addgroup -g 10001 platform \
    && adduser -D -H -u 10001 -G platform platform \
    && mkdir -p /var/log/platform && chown 10001:10001 /var/log/platform
COPY apps/api/platform_api ./platform_api
COPY docs/runbooks /app/runbooks
COPY apps/demo-rag/frontend /app/frontend
COPY apps/worker /app/worker
USER 10001:10001
CMD ["uvicorn", "platform_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "32"]
