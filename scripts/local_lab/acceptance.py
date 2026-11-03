"""Synthetic acceptance against the owned TLS guest and external lab alert receiver."""

import json
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx

from . import vm
from .common import PRIVATE, STATE, save, secret, state, wait
from .deploy import GUEST

COMPOSE = f"sudo docker compose --project-name blueprint-local --file {GUEST}/compose.yaml "


def command(name, operation, **kwargs):
    return vm.ssh(name, COMPOSE + operation, **kwargs)


@contextmanager
def client(*, authenticated=True):
    cfg = state()
    api = httpx.Client(
        base_url=f"https://localhost:{cfg['https_port']}",
        verify=ssl.create_default_context(cafile=str(PRIVATE / "ca.crt")),
        trust_env=False,
        timeout=15,
    )
    if authenticated:
        response = api.post(
            "/api/session",
            headers={"Origin": str(api.base_url).rstrip("/")},
            json={"token": secret("workspace_token").read_text()},
        )
        response.raise_for_status()
        if "Secure" not in response.headers.get("set-cookie", ""):
            raise AssertionError("TLS session cookie is not Secure")
    try:
        yield api
    finally:
        api.close()


def seed(api, label="recovery-reference"):
    result = api.post("/api/collections", json={"name": label})
    result.raise_for_status()
    collection = result.json()["id"]
    result = api.post(
        f"/api/collections/{collection}/sources",
        json={
            "kind": "text",
            "title": "Synthetic recovery reference",
            "text": "QuartzBeacon recovery drill uses a fourteen day retention window.",
        },
    )
    result.raise_for_status()

    def ready():
        response = api.get(f"/api/collections/{collection}/sources")
        response.raise_for_status()
        return response.json() and all(s["status"] == "ready" for s in response.json())

    wait(ready, seconds=90, message="Synthetic worker ingestion did not finish")
    return collection


def query(api, collection):
    response = api.post(
        "/api/chat",
        json={
            "collection_id": collection,
            "question": "QuartzBeacon retention",
            "provider": "offline",
        },
    )
    response.raise_for_status()
    answer = response.json()
    if not answer["sources"] or "fourteen day" not in answer["answer"]:
        raise AssertionError("Restored synthetic source was not retrieved")
    return True


def check(name="primary", *, outage=True):
    cfg = state()
    checks = {}
    with client(authenticated=False) as api:
        checks["unauthenticated_denied"] = api.get("/api/collections").status_code == 401
        checks["origin_denied"] = (
            api.post(
                "/api/session",
                headers={"Origin": "https://untrusted.example"},
                json={"token": secret("workspace_token").read_text()},
            ).status_code
            == 403
        )
        checks["legacy_management_denied"] = all(
            api.get(path).status_code == 404
            for path in ("/documents", "/query", "/metrics", "/docs")
        )
        checks["tls_ui"] = api.get("/").status_code == 200
        with httpx.Client(trust_env=False) as plain:
            redirect = plain.get(f"http://localhost:{cfg['http_port']}/")
            checks["http_redirect"] = redirect.status_code in (
                301,
                302,
                307,
                308,
            ) and redirect.headers["location"].startswith(str(api.base_url))
    with client() as api:
        collection = seed(api)
        checks["worker_ingestion_and_chat_search"] = query(api, collection)
    # Bounded burst against the unauthenticated public UI, not a capacity benchmark.
    with client(authenticated=False) as api, ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda _: api.get("/").status_code, range(80)))
        checks["rate_limit_429"] = 429 in codes
    time.sleep(3)
    with httpx.Client(trust_env=False, timeout=10) as metrics:

        def metric(expression):
            response = metrics.get(
                f"http://localhost:{cfg['metrics_port']}/api/v1/query", params={"query": expression}
            )
            response.raise_for_status()
            return response.json()["data"]["result"]

        wait(
            lambda: (
                metric('up{job="node"}')[0]["value"][1] == "1"
                if metric('up{job="node"}')
                else False
            )
        )
        checks["guest_disk_and_memory_metrics"] = bool(
            metric('node_filesystem_size_bytes{mountpoint="/"}')
            and metric("node_memory_MemTotal_bytes")
        )
    if outage:
        events = STATE / "alerts/events.jsonl"
        before = events.stat().st_size if events.exists() else 0

        def received(status):
            if not events.exists():
                return False
            with events.open() as stream:
                stream.seek(before)
                return any(
                    row.get("alert") == "DatabaseUnavailable" and row.get("status") == status
                    for row in (json.loads(line) for line in stream)
                )

        command(name, "stop db")
        try:
            with client(authenticated=False) as api:
                wait(lambda: api.get("/health/ready").status_code == 503, seconds=30)
                checks["outage_liveness"] = api.get("/health/live").status_code == 200
            wait(
                lambda: received("firing"),
                seconds=90,
                message="External database alert not delivered",
            )
            checks["external_alert_firing"] = True
        finally:
            command(name, "up --detach --wait db")
        wait(
            lambda: received("resolved"),
            seconds=90,
            message="Resolved database alert not delivered",
        )
        checks["external_alert_resolved"] = True
    if not all(checks.values()):
        raise AssertionError({key: value for key, value in checks.items() if not value})
    report = {
        "time_utc": datetime.now(UTC).isoformat(),
        "vm": name,
        "checks": checks,
        "synthetic_collection": collection,
        "scope": "local QEMU guest; no remote exposure or capacity claim",
    }
    save(STATE / "acceptance.json", report)
    print("Local TLS deployment acceptance passed: " + str(len(checks)) + " checks", flush=True)
    return report
