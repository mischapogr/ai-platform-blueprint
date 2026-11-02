#!/usr/bin/env python3
"""Synthetic, offline collection/worker tests. Never needs or calls a provider key."""

import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from backups import backup, restore  # noqa: E402
from common import COMPOSE, ROOT, knowledge_snapshot, run, wait_until


def main():
    collections = []
    with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=15) as api:
        assert api.get("/api/collections").status_code == 401
        login = api.post(
            "/api/session", json={"token": (ROOT / ".secrets/workspace_token").read_text().strip()}
        )
        login.raise_for_status()
        try:
            for i in range(2):
                result = api.post(
                    "/api/collections",
                    json={
                        "name": f"Synthetic integration {i} {uuid.uuid4().hex[:8]}",
                        "allow_external": False,
                    },
                )
                result.raise_for_status()
                collections.append(result.json()["id"])
            one, two = collections
            source = api.post(
                f"/api/collections/{one}/sources",
                json={
                    "kind": "text",
                    "title": "Synthetic orbit reference",
                    "text": (
                        "NebularOrchid is a synthetic name. Its reference code is VIOLET-731. "
                        "Local indexing stores this source only in its chosen collection."
                    ),
                },
            )
            source.raise_for_status()

            def indexed():
                rows = api.get(f"/api/collections/{one}/sources").json()
                assert not any(row["status"] == "failed" for row in rows), rows
                return rows and all(row["status"] == "ready" for row in rows)

            wait_until(indexed, timeout=90)
            query = {
                "question": "NebularOrchid reference code",
                "collection_id": one,
                "provider": "offline",
            }
            response = api.post("/api/chat", json=query)
            response.raise_for_status()
            assert "VIOLET-731" in response.json()["answer"]
            assert response.json()["sources"][0]["source_id"] == source.json()["id"]
            assert (
                api.post("/api/chat", json={**query, "collection_id": two}).json()["sources"] == []
            )
            assert (
                api.post(
                    "/api/chat", json={**query, "provider": "openai", "external_consent": True}
                ).status_code
                == 403
            )
            uploaded = api.post(
                f"/api/collections/{two}/upload",
                files={
                    "file": (
                        "synthetic.txt",
                        b"BlueLantern is synthetic document content. Its code is AZURE-812.",
                        "text/plain",
                    )
                },
            )
            uploaded.raise_for_status()
            wait_until(
                lambda: api.get(f"/api/collections/{two}/sources").json()[0]["status"] == "ready"
            )
            assert (
                "AZURE-812"
                in api.post(
                    "/api/chat", json={"question": "BlueLantern code", "collection_id": two}
                ).json()["answer"]
            )
            # Queue remains durable while worker is stopped, then resumes normally.
            run(COMPOSE + ["stop", "worker"], capture_output=True)
            try:
                queued = api.post(
                    f"/api/collections/{two}/sources",
                    json={
                        "kind": "text",
                        "title": "Queued reference",
                        "text": "DurableQuartz is a pending synthetic background job.",
                    },
                )
                queued.raise_for_status()
                assert any(
                    s["status"] == "queued"
                    for s in api.get(f"/api/collections/{two}/sources").json()
                )
            finally:
                run(COMPOSE + ["start", "worker"], capture_output=True)
            wait_until(
                lambda: all(
                    s["status"] == "ready"
                    for s in api.get(f"/api/collections/{two}/sources").json()
                ),
                timeout=90,
            )
            expected = ROOT / ".local/evidence/knowledge-restore-expected.json"
            expected.parent.mkdir(parents=True, exist_ok=True)
            expected.write_text(json.dumps(knowledge_snapshot()))
            archive = backup()
            restore(archive, expected_kb=expected)
            assert knowledge_snapshot() == json.loads(expected.read_text())
            removed = api.delete("/api/sources/" + source.json()["id"])
            removed.raise_for_status()
            assert api.post("/api/chat", json=query).json()["sources"] == []
            assert "<title>Knowledge Studio" in api.get("/").text
            assert api.get("/assets/app.js").status_code == 200
            assert api.get("/api/usage").status_code == 200
            print(
                "Knowledge integration passed: session auth, worker ingestion, "
                "collection isolation, "
                "text upload, source deletion and worker-stop recovery. No provider calls."
            )
        finally:
            for collection in collections:
                api.delete("/api/collections/" + collection).raise_for_status()


if __name__ == "__main__":
    main()
