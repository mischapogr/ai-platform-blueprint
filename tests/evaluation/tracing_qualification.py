"""End-to-end trace qualification against actual isolated Tempo and PostgreSQL."""

import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from platform_api import db, main, models, telemetry

sys.path.insert(0, "/app/worker")
import main as worker  # noqa: E402

CANARY = "SYNTHETIC-PRIVATE-PAYLOAD-3bd2e854"


def wait(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError("Tempo did not return expected state")


def spans_in(value):
    if isinstance(value, dict):
        if "spanId" in value and "name" in value:
            yield value
        for item in value.values():
            yield from spans_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from spans_in(item)


def canonical_id(value):
    if not value:
        return ""
    if len(value) in (16, 32) and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.lower()
    return base64.b64decode(value).hex()


def run():
    os.environ["PLATFORM_TRACE_ENDPOINT"] = "http://tempo:4318/v1/traces"
    os.environ["WORKSPACE_TOKEN_FILE"] = "/tmp/workspace-token"  # noqa: S108,S105 - fixture path
    os.environ["REQUEST_LOG"] = "/tmp/requests.jsonl"  # noqa: S108
    Path(os.environ["WORKSPACE_TOKEN_FILE"]).write_text(CANARY)
    trace_ids = {}
    with httpx.Client(trust_env=False, timeout=3) as tempo:
        wait(lambda: tempo.get("http://tempo:3200/ready").status_code == 200)
        with TestClient(main.app) as api:
            api.post("/api/session", json={"token": CANARY}).raise_for_status()
            result = api.post("/api/collections", json={"name": "Synthetic tracing collection"})
            result.raise_for_status()
            collection_id = result.json()["id"]

            def enqueue():
                response = api.post(
                    f"/api/collections/{collection_id}/sources",
                    json={
                        "kind": "text",
                        "title": CANARY,
                        "text": "TraceQuartz is a synthetic source. " + CANARY,
                    },
                )
                response.raise_for_status()
                return response.headers["X-Trace-ID"]

            trace_ids["ingestion"] = enqueue()
            worker.execute(worker.claim())
            response = api.post(
                "/api/chat",
                json={
                    "question": "TraceQuartz " + CANARY,
                    "collection_id": collection_id,
                },
            )
            response.raise_for_status()
            assert response.json()["sources"]
            trace_ids["query"] = response.headers["X-Trace-ID"]

            with telemetry.span("qualification.provider-failure"):
                trace_ids["provider_failure"] = telemetry.trace_id()
                try:
                    models.generate(
                        CANARY,
                        [],
                        [],
                        {"id": collection_id, "allow_external": True},
                        models.chat_models()[0],
                    )
                except models.ModelError:
                    pass
                else:
                    raise AssertionError("Expected unconfigured-provider failure")

            with db.connect() as conn:
                conn.execute(
                    "UPDATE kb_collections SET embedding_provider='openai',"
                    "embedding_model='synthetic',dimensions=1536,allow_external=true WHERE id=%s",
                    (collection_id,),
                )
            trace_ids["retry"] = enqueue()
            job = worker.claim()
            worker.execute(job)
            with db.connect() as conn:
                status = conn.execute(
                    "SELECT status FROM kb_jobs WHERE id=%s", (job["id"],)
                ).fetchone()
                assert status["status"] == "queued"
                conn.execute(
                    "UPDATE kb_collections SET embedding_provider='offline',"
                    "embedding_model='hash-v1',dimensions=128,allow_external=false WHERE id=%s",
                    (collection_id,),
                )
                conn.execute("UPDATE kb_jobs SET available_at=now() WHERE id=%s", (job["id"],))
            worker.execute(worker.claim())
        # Lifespan shutdown flushes the API/driver spans; each child flushes itself.
        captured = {}
        for name, trace_id in trace_ids.items():

            def fetch(trace_id=trace_id):
                response = tempo.get(
                    f"http://tempo:3200/api/traces/{trace_id}",
                    headers={"Accept": "application/json"},
                )
                return response.json() if response.status_code == 200 else None

            captured[name] = wait(fetch)
        ingestion = list(spans_in(captured["ingestion"]))
        names = {s["name"] for s in ingestion}
        assert {
            "http.request",
            "queue.enqueue",
            "worker.attempt",
            "ingestion.attempt",
            "ingestion.process",
            "provider.embeddings",
            "db.transaction",
        } <= names, names
        worker_span = next(s for s in ingestion if s["name"] == "worker.attempt")
        child = next(s for s in ingestion if s["name"] == "ingestion.attempt")
        assert canonical_id(child["parentSpanId"]) == canonical_id(worker_span["spanId"])
        assert "retrieval.query" in {s["name"] for s in spans_in(captured["query"])}
        retries = [s for s in spans_in(captured["retry"]) if s["name"] == "worker.attempt"]
        assert len(retries) == 2
        failed_provider = [
            s for s in spans_in(captured["provider_failure"]) if s["name"] == "provider.generate"
        ]
        assert failed_provider and failed_provider[0].get("status", {}).get("code") in (
            2,
            "STATUS_CODE_ERROR",
        )
        assert CANARY not in json.dumps(captured), "Sensitive content reached Tempo"
        assert CANARY not in Path(os.environ["REQUEST_LOG"]).read_text()

    # Exercise an actual refused OTLP connection while HTTP/retrieval stay usable.
    os.environ["PLATFORM_TRACE_ENDPOINT"] = "http://127.0.0.1:9/v1/traces"
    started = time.monotonic()
    with TestClient(main.app) as api:
        assert api.get("/health/live").status_code == 200
        api.post("/api/session", json={"token": CANARY}).raise_for_status()
        response = api.post(
            "/api/chat",
            json={
                "question": "TraceQuartz",
                "collection_id": collection_id,
            },
        )
        response.raise_for_status()
        assert response.json()["sources"]
    assert time.monotonic() - started < 5, "Exporter outage delayed application/shutdown"
    return {
        "summary": {
            "traces": trace_ids,
            "cross_process_parent_verified": True,
            "worker_retry_attempts": 2,
            "privacy_canary_absent": True,
            "exporter_outage_survived": True,
        },
        "failures": [],
    }


if __name__ == "__main__":
    Path("/tmp/benchmark-password").write_text("synthetic-benchmark-only")  # noqa: S108
    print(json.dumps(run()))
