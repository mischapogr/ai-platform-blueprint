"""Lease-based ingestion worker. Only job UUIDs and bounded error codes cross processes."""

import json
import subprocess
import sys
import time

import redis
from platform_api import db
from platform_api.jobs import redis_client


def claim():
    with db.connect() as conn:
        conn.execute(
            "UPDATE kb_jobs SET status='failed',error_code='RETRY_LIMIT',updated_at=now() "
            "WHERE status='running' AND lease_until<now() AND attempts>=3"
        )
        conn.execute(
            "UPDATE kb_sources s SET status='failed',error_code='RETRY_LIMIT' "
            "FROM kb_jobs j WHERE j.source_id=s.id AND j.status='failed' AND s.status='running'"
        )
        job = conn.execute(
            "SELECT id,source_id,attempts FROM kb_jobs WHERE attempts<3 AND "
            "((status='queued' AND available_at<=now()) OR "
            "(status='running' AND lease_until<now())) "
            "ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if job:
            conn.execute(
                "UPDATE kb_jobs SET status='running',attempts=attempts+1,"
                "lease_until=now()+interval '240 seconds',updated_at=now() WHERE id=%s",
                (job["id"],),
            )
            conn.execute(
                "UPDATE kb_sources SET status='running',error_code=NULL WHERE id=%s",
                (job["source_id"],),
            )
        return job


def execute(job):
    retryable = True
    code = "INGESTION_FAILED"
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "platform_api.ingest_job", str(job["id"])],
            capture_output=True,
            timeout=180,
            check=False,
        )  # noqa: S603
        if result.returncode == 0:
            return
        try:
            error = json.loads(result.stdout)
            code = error["code"]
            retryable = error["retryable"]
        except (ValueError, KeyError):
            pass
    except subprocess.TimeoutExpired:
        code = "INGESTION_TIMEOUT"
    status = "queued" if retryable and job["attempts"] + 1 < 3 else "failed"
    delay = 5 * (2 ** job["attempts"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE kb_jobs SET status=%s,error_code=%s,available_at=now()+%s*interval '1 second',"
            "lease_until=NULL,updated_at=now() WHERE id=%s",
            (status, code, delay, job["id"]),
        )
        conn.execute(
            "UPDATE kb_sources SET status=%s,error_code=%s WHERE id=%s",
            (status, code, job["source_id"]),
        )


def main():
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
