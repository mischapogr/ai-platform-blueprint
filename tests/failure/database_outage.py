#!/usr/bin/env python3
"""Intentional local-only incident; always attempt recovery."""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from common import COMPOSE, run, snapshot, wait_until  # noqa: E402


def main():
    before = snapshot()
    with (
        httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=10) as api,
        httpx.Client(trust_env=False, base_url="http://127.0.0.1:9090", timeout=10) as prom,
    ):

        def firing():
            alerts = prom.get("/api/v1/alerts").json()["data"]["alerts"]
            return any(
                a["labels"]["alertname"] == "DatabaseUnavailable" and a["state"] == "firing"
                for a in alerts
            )

        try:
            run(COMPOSE + ["stop", "db"])
            wait_until(lambda: api.get("/health/ready").status_code == 503)
            assert api.get("/health/live").status_code == 200
            response = api.post("/query", json={"question": "backup recovery"})
            assert response.status_code == 503
            wait_until(firing, timeout=75)
            alerts = prom.get("/api/v1/alerts").json()["data"]["alerts"]
            alert = next(a for a in alerts if a["labels"]["alertname"] == "DatabaseUnavailable")
            assert api.get(alert["annotations"]["runbook_url"]).status_code == 200
            print(
                "Outage verified: readiness/query 503, liveness 200, "
                "alert firing, runbook reachable."
            )
        finally:
            run(COMPOSE + ["start", "db"])
            wait_until(lambda: api.get("/health/ready").status_code == 200)
        wait_until(lambda: not firing(), timeout=45)
        result = api.post("/query", json={"question": "PostgreSQL backup recovery", "top_k": 1})
        result.raise_for_status()
        assert result.json()["sources"]
        assert snapshot() == before
    print(
        "Recovery verified: readiness/retrieval restored, alert cleared, "
        "document snapshot unchanged."
    )


if __name__ == "__main__":
    main()
