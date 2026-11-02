#!/usr/bin/env python3
"""Real Compose checks: pgvector, telemetry, and encrypted isolated restore."""

import hashlib
import json
import sys
import uuid
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from backups import backup, restore  # noqa: E402
from common import COMPOSE, ROOT, run, snapshot, wait_until  # noqa: E402


def check_stack(document):
    with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=10) as api:
        first = api.post("/documents", json=document)
        first.raise_for_status()
        second = api.post("/documents", json=document)
        assert first.json() == second.json(), "Document insertion must be idempotent"
        rows = snapshot()
        assert len([row for row in rows if row["id"] == first.json()["id"]]) == 1
        match = api.post(
            "/query", json={"question": document["title"] + " " + document["content"], "top_k": 1}
        )
        match.raise_for_status()
        assert match.json()["sources"][0]["id"] == first.json()["id"]
        assert api.post("/query", json={"question": "?"}).status_code == 422
        assert (
            api.post("/documents", json={"title": "x", "content": "z" * 70000}).status_code == 413
        )
        assert api.get("/runbooks/database-unavailable").status_code == 200
        assert "platform_http_requests_total" in api.get("/metrics").text

    with httpx.Client(trust_env=False, base_url="http://127.0.0.1:9090", timeout=10) as prom:
        wait_until(
            lambda: (
                prom.get("/api/v1/query", params={"query": 'up{job="api"}'}).json()["data"][
                    "result"
                ][0]["value"][1]
                == "1"
            )
        )
        rules = prom.get("/api/v1/rules").json()["data"]["groups"]
        assert any(
            rule["name"] == "DatabaseUnavailable" for group in rules for rule in group["rules"]
        )

    password = (ROOT / ".secrets/grafana_password").read_text().strip()
    with httpx.Client(
        trust_env=False, base_url="http://127.0.0.1:3000", auth=("admin", password), timeout=10
    ) as grafana:
        dashboard = grafana.get("/api/dashboards/uid/platform-overview")
        dashboard.raise_for_status()
        assert len(dashboard.json()["dashboard"]["panels"]) >= 6
        sources = grafana.get("/api/datasources").json()
        assert {"prometheus", "loki"} <= {source["uid"] for source in sources}

        def logs_arrived():
            result = grafana.get(
                "/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
                params={"query": '{job="api"}', "limit": 20},
            )
            result.raise_for_status()
            streams = result.json()["data"]["result"]
            return any(
                '"event": "http_request"' in line
                for stream in streams
                for _, line in stream["values"]
            )

        wait_until(logs_arrived, timeout=90)

    # No writes between snapshot and dump in this local, single-writer test.
    directory = ROOT / ".local/evidence"
    directory.mkdir(parents=True, exist_ok=True)
    expected = directory / "restore-expected.json"
    expected.write_text(json.dumps(snapshot()))
    archive = backup()
    restore(archive, expected)
    assert snapshot() == json.loads(expected.read_text()), "Restore must not change live DB"
    tampered = directory / "tampered.fernet"
    token = bytearray(archive.read_bytes())
    token[-10] = ord("A") if token[-10] != ord("A") else ord("B")
    tampered.write_bytes(token)
    wrong_key = directory / "wrong-key.fernet"
    wrong_key.write_bytes(Fernet(Fernet.generate_key()).encrypt(b"PGDMPinvalid"))
    from cryptography.fernet import InvalidToken

    for invalid in [tampered, wrong_key]:
        try:
            restore(invalid)
        except InvalidToken:
            pass
        else:
            raise AssertionError("Unauthenticated archive was accepted")
    print(
        "Integration passed: pgvector, idempotency, limits, metrics, Grafana, Loki, "
        "full restore comparison, tampering/wrong-key rejection, live DB unchanged."
    )


def remove_test_document(document_id):
    run(
        COMPOSE
        + [
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "platform_admin",
            "-d",
            "platform",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"document_id={document_id}",
        ],
        input=b"DELETE FROM documents WHERE id = :'document_id';\n",
        capture_output=True,
    )


def main():
    document = {
        "title": "Restore sentinel " + uuid.uuid4().hex,
        "content": "Encrypted PostgreSQL backup recovery sentinel verification",
    }
    document_id = hashlib.sha256(
        (document["title"] + "\0" + document["content"]).encode()
    ).hexdigest()
    before = snapshot()
    try:
        check_stack(document)
    finally:
        # Admin-only fixture cleanup, never an application DELETE capability.
        remove_test_document(document_id)
    assert snapshot() == before, "Integration fixture cleanup changed unrelated documents"
    print("Integration fixture removed; original document snapshot preserved.")


if __name__ == "__main__":
    main()
