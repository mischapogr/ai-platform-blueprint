"""Bounded Tempo outage: application still works and its loss is externally alerted."""

import json
from datetime import UTC, datetime

from .acceptance import client, command
from .common import STATE, save, wait
from .diagnostics import grafana_client


def check(name):
    receipt = STATE / "observability-receipt.json"
    if not receipt.exists():
        raise RuntimeError("Run make lab-monitoring-test first to create synthetic evidence")
    collection = json.loads(receipt.read_text())["synthetic_collection"]
    events = STATE / "alerts/events.jsonl"
    offset = events.stat().st_size if events.exists() else 0

    def notified(status):
        if not events.exists():
            return False
        with events.open() as stream:
            stream.seek(offset)
            return any(
                row.get("alert") == "MonitoringTargetUnavailable"
                and row.get("component") == "tempo"
                and row.get("status") == status
                for row in (json.loads(line) for line in stream)
            )

    checks = {}
    with grafana_client() as grafana:
        response = grafana.get(
            "api/datasources/proxy/uid/prometheus/api/v1/query", params={"query": "up"}
        )
        response.raise_for_status()
        if not all(row["value"][1] == "1" for row in response.json()["data"]["result"]):
            raise RuntimeError("Start the fault drill only when all scrape targets are UP")
        command(name, "stop tempo")
        try:
            with client() as api:
                checks["api_ready_during_trace_outage"] = (
                    api.get("/health/ready").status_code == 200
                )
                response = api.post(
                    "/api/chat",
                    json={
                        "collection_id": collection,
                        "question": "UploadQuartz synthetic PDF",
                        "provider": "offline",
                    },
                )
                response.raise_for_status()
                checks["retrieval_during_trace_outage"] = bool(response.json()["sources"])

            def firing():
                response = grafana.get(
                    "api/datasources/proxy/uid/prometheus/api/v1/query",
                    params={
                        "query": (
                            'ALERTS{alertname="MonitoringTargetUnavailable",'
                            'job="tempo",alertstate="firing"}'
                        )
                    },
                )
                return response.status_code == 200 and bool(response.json()["data"]["result"])

            wait(firing, seconds=90, message="Tempo outage alert did not fire")
            wait(
                lambda: notified("firing"),
                seconds=30,
                message="Tempo alert not externally delivered",
            )
            checks["monitoring_target_alert_delivered"] = True
        finally:
            command(name, "up --detach tempo")
        wait(
            lambda: grafana.get("api/datasources/proxy/uid/tempo/ready").status_code == 200,
            seconds=120,
            message="Tempo did not recover",
        )
        wait(
            lambda: notified("resolved"),
            seconds=90,
            message="Tempo resolution not externally delivered",
        )
        checks["monitoring_target_resolution_delivered"] = True
    if not all(checks.values()):
        raise AssertionError(checks)
    report = {
        "time_utc": datetime.now(UTC).isoformat(),
        "vm": name,
        "checks": checks,
        "scope": "bounded trace-backend outage; local independent receiver; no human paging",
    }
    save(STATE / "monitoring-outage-receipt.json", report)
    print(json.dumps(report, indent=2))
    return report
