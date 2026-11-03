"""Real PostgreSQL/crash tests; run only in the disposable evaluation container."""

import json
import multiprocessing
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from platform_api import db, ingest_job, jobs, knowledge, models

sys.path.insert(0, "/app/worker")
import main as worker  # noqa: E402


def paused_child(job, phase, ready, release):
    """Test-only pause points: no fault switches are added to the application."""
    if phase == "claimed":
        ready.set()
        release.wait(20)
    if phase == "processing":
        original = models.embeddings

        def embeddings(*args):
            ready.set()
            if not release.wait(20):
                raise RuntimeError("Test release timed out")
            return original(*args)

        models.embeddings = embeddings
    if phase in {"before-commit", "after-commit"}:
        original_connect = db.connect

        class Proxy:
            final_write = False

            def __init__(self, conn):
                self.conn = conn

            def execute(self, query, *args):
                result = self.conn.execute(query, *args)
                if query.startswith("UPDATE kb_jobs SET status='ready'"):
                    self.final_write = True
                    if phase == "before-commit":
                        ready.set()
                        release.wait(20)
                return result

            def cursor(self):
                return self.conn.cursor()

        @contextmanager
        def connection():
            with original_connect() as conn:
                proxy = Proxy(conn)
                yield proxy
            if proxy.final_write and phase == "after-commit":
                ready.set()
                release.wait(20)

        db.connect = connection
    ingest_job.process(job["id"], job["attempt_token"])


@contextmanager
def paused(job, phase):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=paused_child, args=(job, phase, ready, release))
    process.start()
    try:
        assert ready.wait(10), "Child did not reach deterministic fault boundary"
        yield process, release
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        assert not process.is_alive(), "Fault-test process did not stop"


def state(source_id):
    with db.connect() as conn:
        source = conn.execute("SELECT status FROM kb_sources WHERE id=%s", (source_id,)).fetchone()
        job = conn.execute(
            "SELECT status,attempts,error_code,attempt_token FROM kb_jobs WHERE source_id=%s",
            (source_id,),
        ).fetchone()
        chunks = conn.execute(
            "SELECT id,ordinal,content,embedding::text FROM kb_chunks WHERE source_id=%s "
            "ORDER BY ordinal",
            (source_id,),
        ).fetchall()
    return {"source": source, "job": job, "chunks": chunks}


def expire(job):
    with db.connect() as conn:
        conn.execute(
            "UPDATE kb_jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )


def fixture(collection, *, kind="text", locator="", payload=b"SyntheticQuartz durable queue fact."):
    source = jobs.create_source(collection["id"], "Synthetic queue case", kind, locator, payload)
    job = worker.claim()
    assert job and str(job["source_id"]) == source["id"]
    return source["id"], job


def clean(collection):
    with db.connect() as conn:
        conn.execute("DELETE FROM kb_sources WHERE collection_id=%s", (collection["id"],))


def run():
    collection = knowledge.create_collection(knowledge.CollectionInput(name="Queue qualification"))
    checks = []
    for phase in ("claimed", "processing", "before-commit", "after-commit"):
        source, job = fixture(collection)
        with paused(job, phase) as (process, _):
            process.kill()
            process.join(5)
        snapshot = state(source)
        if phase == "after-commit":
            assert snapshot["job"]["status"] == snapshot["source"]["status"] == "ready"
            assert len(snapshot["chunks"]) == 1
            assert worker.claim() is None
        else:
            assert snapshot["job"]["status"] == snapshot["source"]["status"] == "running"
            assert snapshot["chunks"] == []
            expire(job)
            replacement = worker.claim()
            assert replacement["attempt_token"] != job["attempt_token"]
            worker.execute(replacement)
            snapshot = state(source)
            assert snapshot["job"]["status"] == snapshot["source"]["status"] == "ready"
            assert snapshot["job"]["attempts"] == 2 and len(snapshot["chunks"]) == 1
        checks.append("crash-" + phase)
        clean(collection)

    source, stale = fixture(collection)
    with paused(stale, "processing") as (process, release):
        expire(stale)
        current = worker.claim()
        worker.execute(current)
        before = state(source)
        release.set()
        process.join(10)
        assert process.exitcode == 0
        worker.fail(stale, "INGESTION_FAILED", True)
        assert state(source) == before, "Stale success/failure modified current result"
    checks.append("stale-success-and-failure-fenced")
    clean(collection)

    source, job = fixture(collection)
    expire(job)
    ingest_job.process(job["id"], job["attempt_token"])
    worker.fail(job, "INGESTION_FAILED", False)
    assert state(source)["chunks"] == [] and state(source)["job"]["status"] == "running"
    # Expiry alone must invalidate both final writers, before reclamation too.
    for _ in range(2):
        job = worker.claim()
        expire(job)
    assert worker.claim() is None
    snapshot = state(source)
    assert snapshot["job"]["attempts"] == 3
    assert snapshot["job"]["error_code"] == "RETRY_LIMIT"
    assert snapshot["source"]["status"] == snapshot["job"]["status"] == "failed"
    assert snapshot["job"]["attempt_token"] is None
    checks.append("expiry-and-crash-retry-exhaustion")
    # A manual retry resets attempts but must not revive the old token (ABA case).
    knowledge.retry_source(uuid.UUID(source))
    current = worker.claim()
    assert current["attempts"] == 1 and current["attempt_token"] != job["attempt_token"]
    worker.execute(current)
    before = state(source)
    worker.fail(job, "INGESTION_FAILED", True)
    assert state(source) == before
    checks.append("manual-retry-fences-old-token")
    clean(collection)

    source, job = fixture(collection, kind="file", locator="unsupported.exe")
    worker.execute(job)
    snapshot = state(source)
    assert snapshot["job"]["status"] == snapshot["source"]["status"] == "failed"
    assert snapshot["job"]["attempts"] == 1
    assert snapshot["job"]["error_code"] == "UNSUPPORTED_FILE_TYPE"
    checks.append("non-retryable-child-error")
    clean(collection)

    source, job = fixture(collection)
    with patch.object(
        worker.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 180)
    ):
        worker.execute(job)
    snapshot = state(source)
    assert snapshot["job"]["status"] == snapshot["source"]["status"] == "queued"
    assert snapshot["job"]["error_code"] == "INGESTION_TIMEOUT" and snapshot["chunks"] == []
    checks.append("timeout-handler-requeues-owned-attempt")
    clean(collection)

    # Real provider adapter fails locally before any network call; no key is mounted.
    with db.connect() as conn:
        conn.execute(
            "UPDATE kb_collections SET embedding_provider='openai',embedding_model='synthetic',"
            "dimensions=1536,allow_external=true WHERE id=%s",
            (collection["id"],),
        )
    source, job = fixture(collection)
    for attempt in range(1, 4):
        worker.execute(job)
        snapshot = state(source)
        assert snapshot["job"]["attempts"] == attempt
        assert snapshot["job"]["status"] == ("queued" if attempt < 3 else "failed")
        if attempt < 3:
            assert worker.claim() is None, "Backoff was not respected"
            with db.connect() as conn:
                conn.execute("UPDATE kb_jobs SET available_at=now() WHERE id=%s", (job["id"],))
            job = worker.claim()
    checks.append("provider-error-bounded-retries-and-backoff")
    clean(collection)
    with db.connect() as conn:
        conn.execute(
            "UPDATE kb_collections SET embedding_provider='offline',embedding_model='hash-v1',"
            "dimensions=128,allow_external=false WHERE id=%s",
            (collection["id"],),
        )

    source, job = fixture(collection)
    with paused(job, "processing") as (process, release):
        knowledge.delete_source(uuid.UUID(source))
        release.set()
        process.join(10)
        assert process.exitcode == 0
        assert state(source) == {"source": None, "job": None, "chunks": []}
    checks.append("deletion-during-processing")

    started = time.monotonic()
    source, job = fixture(collection)
    worker.execute(job)
    assert state(source)["job"]["status"] == "ready"
    assert time.monotonic() - started < 5, "Best-effort Redis wakeup delayed durable processing"
    checks.append("redis-unavailable-durable-processing")
    clean(collection)

    # Hold the first source in one transaction. A claimant must skip it and
    # process another source rather than block or claim the same job twice.
    first = jobs.create_source(collection["id"], "First", "text", "", b"First synthetic source.")
    second = jobs.create_source(collection["id"], "Second", "text", "", b"Second synthetic source.")
    with db.connect() as conn:
        conn.execute("SELECT id FROM kb_sources WHERE id=%s FOR UPDATE", (first["id"],))
        job = worker.claim()
        assert str(job["source_id"]) == second["id"]
        assert worker.claim() is None
    another = worker.claim()
    assert str(another["source_id"]) == first["id"]
    checks.append("concurrent-claim-skips-locked-source")
    clean(collection)
    return {"summary": {"checks": len(checks), "passed": checks}, "failures": []}


if __name__ == "__main__":
    Path("/tmp/benchmark-password").write_text("synthetic-benchmark-only")  # noqa: S108
    print(json.dumps(run()))
