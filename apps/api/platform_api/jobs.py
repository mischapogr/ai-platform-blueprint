"""PostgreSQL is the durable queue; Redis only reduces idle polling latency."""

import os
import uuid

import redis
from prometheus_client import Gauge
from redis.backoff import NoBackoff
from redis.retry import Retry

from . import db, models, telemetry

BACKLOG = Gauge("platform_jobs", "Durable job count", ["status"])
WORKER_AGE = Gauge("platform_worker_heartbeat_age_seconds", "Seconds since worker heartbeat")


def redis_client():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        socket_connect_timeout=1,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


def notify():
    try:
        redis_client().lpush("kb:wakeup", "1")
        redis_client().ltrim("kb:wakeup", 0, 99)
    except redis.RedisError:
        pass  # The committed PostgreSQL job remains available for polling.


@telemetry.traced("queue.enqueue")
def create_source(collection_id, title, kind, locator, payload, max_pages=5):
    source_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    telemetry.annotate(**{"job.id": job_id})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO kb_sources (id,collection_id,title,kind,locator,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (source_id, collection_id, title, kind, locator, payload),
        )
        conn.execute(
            "INSERT INTO kb_jobs (id,source_id,trace_parent) VALUES (%s,%s,%s)",
            (job_id, source_id, telemetry.traceparent()),
        )
    notify()
    return {"id": source_id, "job_id": job_id, "status": "queued"}


def lock_attempt(conn, job_id, token):
    """Serialize writers in source -> job order and reject expired/stale owners."""
    source = conn.execute(
        "SELECT id FROM kb_sources WHERE id=(SELECT source_id FROM kb_jobs WHERE id=%s) FOR UPDATE",
        (job_id,),
    ).fetchone()
    if not source:
        return None
    return conn.execute(
        "SELECT * FROM kb_jobs WHERE id=%s AND status='running' AND attempt_token=%s "
        "AND lease_until>clock_timestamp() FOR UPDATE",
        (job_id, token),
    ).fetchone()


def update_metrics():
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT status,count(*) AS count FROM kb_jobs GROUP BY status"
            ).fetchall()
        with db.connect() as conn:
            usage = conn.execute(
                "SELECT model,operation,sum(input_tokens) AS input_tokens, "
                "sum(output_tokens) AS output_tokens,count(*) AS calls "
                "FROM kb_usage GROUP BY model,operation"
            ).fetchall()
        for record in usage:
            for direction in ("input", "output"):
                tokens = record[direction + "_tokens"]
                if tokens is not None:
                    models.RECORDED_TOKENS.labels(
                        record["model"], record["operation"], direction
                    ).set(tokens)
            models.RECORDED_CALLS.labels(record["model"], record["operation"]).set(record["calls"])
        counts = {row["status"]: row["count"] for row in rows}
        for status in ["queued", "running", "ready", "failed"]:
            BACKLOG.labels(status).set(counts.get(status, 0))
        heartbeat = redis_client().get("kb:heartbeat")
        import time

        WORKER_AGE.set(time.time() - float(heartbeat) if heartbeat else 9999)
    except (OSError, redis.RedisError):
        WORKER_AGE.set(9999)
