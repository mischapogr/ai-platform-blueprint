"""Live PDF upload + metrics/log/trace checks through the actual guest TLS proxy."""

import json
import re
import sys
import time
from datetime import UTC, datetime

from .acceptance import client
from .common import ROOT, STATE, save, wait
from .diagnostics import grafana_client

sys.path.insert(0, str(ROOT / "tests/evaluation"))
from pdf_fixture import CANARY, synthetic_pdf  # noqa: E402


def spans(value):
    if isinstance(value, dict):
        if "spanId" in value and "name" in value:
            yield value
        for child in value.values():
            yield from spans(child)
    elif isinstance(value, list):
        for child in value:
            yield from spans(child)


def check(name="recovery"):
    checks = {}
    records = []
    with client() as api, grafana_client() as grafana:
        for uid in ("prometheus", "loki", "tempo"):

            def healthy(uid=uid):
                if uid == "tempo":
                    # Grafana's Tempo plugin does not implement its health RPC.
                    response = grafana.get("api/datasources/proxy/uid/tempo/ready")
                    return response.status_code == 200
                response = grafana.get(f"api/datasources/uid/{uid}/health")
                return response.status_code == 200 and response.json().get("status") == "OK"

            wait(healthy, seconds=120, message=uid + " datasource not healthy through Grafana")
            checks[uid + "_datasource"] = True
        for endpoint in ("api/dashboards/uid/platform-overview", "api/health"):
            response = grafana.get(endpoint)
            response.raise_for_status()
        checks["provisioned_dashboard"] = True

        def targets_ready():
            response = grafana.get(
                "api/datasources/proxy/uid/prometheus/api/v1/query", params={"query": "up"}
            )
            if response.status_code != 200:
                return False
            states = {
                row["metric"]["job"]: row["value"][1] for row in response.json()["data"]["result"]
            }
            expected = {
                "api",
                "proxy",
                "node",
                "loki",
                "tempo",
                "alloy",
                "grafana",
                "prometheus",
                "alertmanager",
            }
            return expected <= states.keys() and all(states[name] == "1" for name in expected)

        wait(targets_ready, seconds=90, message="Configured metrics targets are missing or down")
        checks["all_nine_scrape_targets_up"] = True
        with grafana_client() as anonymous:
            anonymous.auth = None
            checks["grafana_requires_login"] = anonymous.get("api/datasources").status_code == 401
        response = api.post(
            "/api/collections",
            json={
                "name": "PDF diagnostics – synthetic " + datetime.now(UTC).strftime("%H:%M:%S UTC")
            },
        )
        response.raise_for_status()
        collection = response.json()["id"]
        for size in (96 * 1024, 1536 * 1024):
            data = synthetic_pdf(size)
            response = api.post(
                f"/api/collections/{collection}/upload",
                files={"file": ("synthetic-" + str(size) + ".pdf", data, "application/pdf")},
            )
            if response.status_code != 202:
                raise AssertionError(
                    "Synthetic PDF upload returned HTTP " + str(response.status_code)
                )
            trace = response.headers.get("X-Trace-ID", "")
            assert re.fullmatch(r"[a-f0-9]{32}", trace)
            records.append(
                {
                    "pdf_bytes": len(data),
                    "trace_id": trace,
                    "source_id": response.json()["id"],
                    "job_id": response.json()["job_id"],
                }
            )

        assert len({record["trace_id"] for record in records}) == len(records), (
            "Independent PDF uploads inherited the same trace"
        )
        checks["independent_upload_traces"] = True

        def indexed():
            response = api.get(f"/api/collections/{collection}/sources")
            response.raise_for_status()
            rows = response.json()
            if any(row["status"] == "failed" for row in rows):
                raise AssertionError("Synthetic valid PDF ingestion failed")
            return len(rows) == 2 and all(row["status"] == "ready" for row in rows)

        wait(indexed, seconds=90)
        response = api.post(
            "/api/chat",
            json={
                "collection_id": collection,
                "question": "UploadQuartz synthetic PDF",
                "provider": "offline",
            },
        )
        response.raise_for_status()
        checks["pdf_ingestion_and_retrieval"] = bool(response.json()["sources"])
        checks["pdf_above_64KiB_and_1MiB_accepted"] = True
        # Application limit and reverse-proxy limit remain distinct, neither is disabled.
        response = api.post(
            f"/api/collections/{collection}/upload",
            files={
                "file": (
                    "synthetic-too-large.pdf",
                    b"X" * (10 * 1024 * 1024 + 1),
                    "application/pdf",
                )
            },
        )
        checks["application_10MiB_limit"] = response.status_code == 413 and bool(
            response.json().get("detail")
        )
        api_rejection_trace = response.headers.get("X-Trace-ID")
        response = api.post(
            f"/api/collections/{collection}/upload",
            files={"file": ("synthetic-proxy-limit.pdf", b"X" * 11000001, "application/pdf")},
        )
        checks["proxy_upload_limit"] = response.status_code == 413
        response = api.post("/api/session", json={"padding": "X" * 70000})
        checks["general_64KiB_limit_preserved"] = response.status_code == 413
        response = api.post(
            f"/api/collections/{collection}/upload",
            files={"file": ("synthetic-unreadable.pdf", b"not-a-valid-pdf", "application/pdf")},
        )
        response.raise_for_status()
        bad = {
            "source_id": response.json()["id"],
            "job_id": response.json()["job_id"],
            "trace_id": response.headers["X-Trace-ID"],
        }
        assert bad["trace_id"] not in {record["trace_id"] for record in records}

        def failed():
            rows = api.get(f"/api/collections/{collection}/sources").json()
            return any(
                row["id"] == bad["source_id"]
                and row["status"] == "failed"
                and row["error_code"] == "PDF_PARSE_FAILED"
                for row in rows
            )

        wait(failed, seconds=60)
        checks["worker_parse_failure_distinguished"] = True
        for record in [*records, bad]:

            def trace_ready(record=record):
                response = grafana.get(
                    "api/datasources/proxy/uid/tempo/api/traces/" + record["trace_id"]
                )
                if response.status_code != 200:
                    return False
                value = response.json()
                captured_spans = list(spans(value))
                names = {span["name"] for span in captured_spans}
                job_ids = {
                    attribute["value"].get("stringValue")
                    for span in captured_spans
                    for attribute in span.get("attributes", [])
                    if attribute.get("key") == "job.id"
                }
                if job_ids != {record["job_id"]}:
                    return False
                requests = [span for span in captured_spans if span["name"] == "http.request"]
                if len(requests) != 1 or requests[0].get("parentSpanId"):
                    return False
                if {
                    "http.request",
                    "queue.enqueue",
                    "worker.attempt",
                    "ingestion.attempt",
                } <= names:
                    assert CANARY not in json.dumps(value)
                    return sorted(names)
                return False

            record["span_names"] = wait(
                trace_ready, seconds=60, message="Upload-to-worker trace incomplete"
            )

            def logged(record=record):
                response = grafana.get(
                    "api/datasources/proxy/uid/loki/loki/api/v1/query_range",
                    params={
                        "query": '{job="api"} |= "' + record["trace_id"] + '"',
                        "start": str(int((time.time() - 300) * 1e9)),
                        "limit": "10",
                    },
                )
                if response.status_code != 200:
                    return False
                value = response.json()
                assert CANARY not in json.dumps(value)
                return bool(value.get("data", {}).get("result"))

            wait(logged, seconds=60, message="API request did not reach Loki")

        def proxy_failures():
            response = grafana.get(
                "api/datasources/proxy/uid/loki/loki/api/v1/query_range",
                params={
                    "query": '{job="proxy"} | json | DownstreamStatus = 413',
                    "start": str(int((time.time() - 300) * 1e9)),
                    "limit": "100",
                },
            )
            if response.status_code != 200:
                return False
            value = response.json()
            if not value.get("data", {}).get("result"):
                return False
            rows = [
                json.loads(row[1]) for stream in value["data"]["result"] for row in stream["values"]
            ]
            assert all(
                not any(key in row for key in ("RequestPath", "RequestHost", "ClientHost"))
                for row in rows
            )
            return any(row.get("RouterName") == "upload@file" for row in rows) and any(
                row.get("RouterName") == "workspace@file" for row in rows
            )

        wait(proxy_failures, seconds=60, message="Proxy rejections did not reach Loki")
        checks["proxy_rejections_visible_in_loki"] = True
        checks["api_logs_link_to_complete_tempo_worker_traces"] = True
        checks["document_content_absent_from_logs_and_traces"] = True
    if not all(checks.values()):
        raise AssertionError({key: value for key, value in checks.items() if not value})
    report = {
        "time_utc": datetime.now(UTC).isoformat(),
        "vm": name,
        "checks": checks,
        "synthetic_collection": collection,
        "valid_uploads": records,
        "worker_parse_failure": bad,
        "api_size_rejection_trace": api_rejection_trace,
        "scope": (
            "real local TLS proxy, PDF parser/worker, Grafana, Loki and Tempo; synthetic files only"
        ),
    }
    save(STATE / "observability-receipt.json", report)
    print(
        json.dumps(
            {
                "checks": checks,
                "synthetic_collection": collection,
                "example_upload_trace": records[-1]["trace_id"],
                "example_failed_worker_trace": bad["trace_id"],
            },
            indent=2,
        )
    )
    return report
