"""Lease-based ingestion worker. Only job UUIDs and bounded error codes cross processes."""

import json
import os
import subprocess
import sys
import time
import uuid

import redis
from platform_api import db, jobs, telemetry
from platform_api.jobs import redis_client


def claim():
    with db.connect() as conn:
        # Source-first locking matches deletion, manual retry and final persistence.
        expired = conn.execute(
            "SELECT s.id FROM kb_sources s JOIN kb_jobs j ON j.source_id=s.id "
            "WHERE j.status='running' AND j.lease_until<clock_timestamp() AND j.attempts>=3 "
            "FOR UPDATE OF s SKIP LOCKED"
        ).fetchall()
        for source in expired:
            updated = conn.execute(
                "UPDATE kb_jobs SET status='failed',error_code='RETRY_LIMIT',"
                "attempt_token=NULL,lease_until=NULL,updated_at=now() WHERE source_id=%s "
                "AND status='running' AND lease_until<clock_timestamp() AND attempts>=3",
                (source["id"],),
            )
            if updated.rowcount:
                conn.execute(
                    "UPDATE kb_sources SET status='failed',error_code='RETRY_LIMIT' WHERE id=%s",
                    (source["id"],),
                )
        candidate = conn.execute(
            "SELECT j.id FROM kb_sources s JOIN kb_jobs j ON j.source_id=s.id "
            "WHERE j.attempts<3 AND ((j.status='queued' AND j.available_at<=now()) OR "
            "(j.status='running' AND j.lease_until<clock_timestamp())) "
            "ORDER BY j.available_at,j.id FOR UPDATE OF s SKIP LOCKED LIMIT 1"
        ).fetchone()
        if not candidate:
            return None
        job = conn.execute(
            "UPDATE kb_jobs SET status='running',attempts=attempts+1,attempt_token=%s,"
            "lease_until=now()+interval '240 seconds',updated_at=now() WHERE id=%s AND attempts<3 "
            "AND ((status='queued' AND available_at<=now()) OR "
            "(status='running' AND lease_until<clock_timestamp())) RETURNING *",
            (uuid.uuid4(), candidate["id"]),
        ).fetchone()
        if not job:
            return None
        conn.execute(
            "UPDATE kb_sources SET status='running',error_code=NULL WHERE id=%s",
            (job["source_id"],),
        )
        return job


def fail(job, code, retryable):
    with db.connect() as conn:
        current = jobs.lock_attempt(conn, job["id"], job["attempt_token"])
        if not current:
            return  # A stale failure must not requeue or fail a newer attempt.
        status = "queued" if retryable and current["attempts"] < 3 else "failed"
        delay = 5 * (2 ** (current["attempts"] - 1))
        conn.execute(
            "UPDATE kb_jobs SET status=%s,error_code=%s,available_at=now()+%s*interval '1 second',"
            "lease_until=NULL,attempt_token=NULL,updated_at=now() WHERE id=%s",
            (status, code, delay, job["id"]),
        )
        conn.execute(
            "UPDATE kb_sources SET status=%s,error_code=%s WHERE id=%s",
            (status, code, job["source_id"]),
        )


def _execute(job):
    retryable = True
    code = "INGESTION_FAILED"
    try:
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "platform_api.ingest_job",
                str(job["id"]),
                str(job["attempt_token"]),
            ],
            env={**os.environ, "PLATFORM_TRACE_PARENT": telemetry.traceparent() or ""},
            capture_output=True,
            timeout=180,
            check=False,
        )  # noqa: S603
        if result.returncode == 0:
            return "child-exited"
        try:
            error = json.loads(result.stdout)
            code = error["code"]
            retryable = error["retryable"]
        except (ValueError, KeyError):
            pass
    except subprocess.TimeoutExpired:
        code = "INGESTION_TIMEOUT"
    fail(job, code, retryable)
    return (
        "timeout"
        if code == "INGESTION_TIMEOUT"
        else ("retryable-error" if retryable else "terminal-error")
    )


def execute(job):
    with telemetry.span(
        "worker.attempt",
        parent=job.get("trace_parent"),
        **{"job.id": str(job["id"]), "job.attempt": job["attempts"]},
    ):
        telemetry.annotate(**{"operation.outcome": _execute(job)})


def main():
    telemetry.configure("platform-worker")
    while True:
        try:
            try:
                redis_client().set("kb:heartbeat", str(time.time()), ex=600)
            except redis.RedisError:
                pass
            job = claim()
            if job:
                execute(job)
            else:
                try:
                    redis_client().blpop("kb:wakeup", timeout=1)
                except redis.RedisError:
                    time.sleep(1)
        except Exception:
            # Daemon logs contain no document, URL, SQL statement or credential.
            print("Worker dependency unavailable; retrying", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
