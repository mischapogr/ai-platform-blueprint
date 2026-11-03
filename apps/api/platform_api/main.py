import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field, field_validator

from . import db, telemetry
from .retrieval import embed, vector_literal

REQUESTS = Counter("platform_http_requests_total", "HTTP requests", ["route", "method", "status"])
LATENCY = Histogram("platform_http_request_duration_seconds", "Request duration", ["route"])
DATABASE = Gauge("platform_database_up", "Last database probe success")
QUERIES = Counter("platform_queries_total", "Retrieval queries", ["outcome"])
logger = logging.getLogger("platform.requests")
logger.setLevel(logging.INFO)
logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_path = Path(os.environ.get("REQUEST_LOG", "/tmp/platform-requests.jsonl"))  # noqa: S108
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=2)
    logger.addHandler(handler)
    telemetry.configure("platform-api")
    try:
        yield
    finally:
        telemetry.shutdown()
        logger.removeHandler(handler)
        handler.close()


app = FastAPI(title="AI Platform Reference – local knowledge workspace", lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    started = time.perf_counter()
    status = 500
    with telemetry.span("http.request", parent=request.headers.get("traceparent")) as active:
        try:
            response = await call_next(request)
            status = response.status_code
            if telemetry.trace_id():
                response.headers["X-Trace-ID"] = telemetry.trace_id()
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            method = (
                request.method if request.method in {"GET", "POST", "HEAD", "OPTIONS"} else "OTHER"
            )
            duration = time.perf_counter() - started
            telemetry.attributes(
                active, **{"http.route": route, "http.method": method, "http.status_code": status}
            )
            REQUESTS.labels(route, method, str(status)).inc()
            LATENCY.labels(route).observe(duration)
            if route != "/metrics":
                logger.info(
                    json.dumps(
                        {
                            "event": "http_request",
                            "route": route,
                            "method": method,
                            "status": status,
                            "trace_id": telemetry.trace_id(),
                            "duration_seconds": round(duration, 6),
                        }
                    )
                )


def database_ready() -> bool:
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        DATABASE.set(1)
        return True
    except (psycopg.Error, OSError):
        DATABASE.set(0)
        return False


def unavailable():
    DATABASE.set(0)
    return HTTPException(status_code=503, detail="Database unavailable; see database runbook")


class Document(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("title", "content")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Text must not be blank")
        embed(value)
        return value


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=5)

    @field_validator("question")
    @classmethod
    def meaningful(cls, value):
        embed(value)
        return value


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    if not database_ready():
        raise unavailable()
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    if database_ready():
        from .jobs import update_metrics

        try:
            update_metrics()
        except psycopg.Error:
            pass
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.post("/documents", status_code=201)
def add_document(document: Document):
    digest = hashlib.sha256((document.title + "\0" + document.content).encode()).hexdigest()
    vector = vector_literal(embed(document.title + " " + document.content))
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO documents (id, title, content, embedding) "
                "VALUES (%s, %s, %s, %s::vector) "
                "ON CONFLICT (id) DO NOTHING",
                (digest, document.title, document.content, vector),
            )
        DATABASE.set(1)
    except (psycopg.Error, OSError):
        raise unavailable() from None
    return {"id": digest}


@app.post("/query")
def query(payload: Query):
    vector = vector_literal(embed(payload.question))
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, title, content, 1 - (embedding <=> %s::vector) AS score "
                "FROM documents WHERE 1 - (embedding <=> %s::vector) >= 0.15 "
                "ORDER BY embedding <=> %s::vector, id LIMIT %s",
                (vector, vector, vector, payload.top_k),
            ).fetchall()
        DATABASE.set(1)
    except (psycopg.Error, OSError):
        QUERIES.labels("error").inc()
        raise unavailable() from None
    QUERIES.labels("answered" if rows else "no_match").inc()
    return {
        "mode": "offline-extractive",
        "answer": "\n\n".join(row["content"] for row in rows)
        if rows
        else "No matching sources found.",
        "sources": [
            {"id": row["id"], "title": row["title"], "score": row["score"]} for row in rows
        ],
        "provider_calls": 0,
    }


@app.get("/runbooks/database-unavailable", response_class=PlainTextResponse)
def database_runbook():
    return Path("/app/runbooks/DATABASE-UNAVAILABLE.md").read_text()


@app.get("/runbooks/knowledge-ingestion", response_class=PlainTextResponse)
def knowledge_runbook():
    return Path("/app/runbooks/KNOWLEDGE-INGESTION.md").read_text()


# New workspace endpoints are authenticated; the legacy synthetic demo tables stay separate.
from .knowledge import router, session_router  # noqa: E402

app.include_router(session_router)
app.include_router(router)


@app.exception_handler(psycopg.Error)
async def database_error(request, error):
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Do not echo uploaded text, questions or credentials in validation responses.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                for item in error.errors()
            ]
        },
    )


FRONTEND = Path(os.getenv("FRONTEND_DIR", "/app/frontend"))
if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(FRONTEND / "index.html")
