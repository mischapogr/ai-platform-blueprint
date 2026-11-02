FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && groupadd --gid 10001 platform \
    && useradd --uid 10001 --gid 10001 --no-create-home platform \
    && mkdir -p /var/log/platform && chown 10001:10001 /var/log/platform
COPY apps/api/platform_api ./platform_api
COPY docs/runbooks /app/runbooks
COPY apps/demo-rag/frontend /app/frontend
COPY apps/worker /app/worker
USER 10001:10001
CMD ["uvicorn", "platform_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "32"]
