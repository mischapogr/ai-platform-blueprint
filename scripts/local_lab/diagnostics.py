"""Read-only, content-free diagnostics for the owned guest deployment."""

import json
import re
import shlex
import ssl
import subprocess
from collections import Counter
from datetime import UTC, datetime

import httpx

from . import registry, vm
from .acceptance import client, command
from .common import PRIVATE, STATE, compose, save, secret, state
from .observability import PROXY_LOG_FIELDS


def proxy_events(output):
    """Never return raw log lines, paths, headers or arbitrary error messages."""
    events = []
    for line in output.splitlines():
        raw = line.split("|", 1)[-1].strip()
        try:
            row = json.loads(raw)
        except ValueError:
            if "Maximum size 65536 was reached" in raw:
                events.append({"component": "proxy", "reason": "general_64KiB_limit"})
            elif "read-only file system" in raw:
                events.append({"component": "proxy", "reason": "read_only_buffer_storage"})
            continue
        filtered = {}
        for key in PROXY_LOG_FIELDS:
            value = row.get(key)
            if key in {"RouterName", "ServiceName", "RequestMethod"}:
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", value):
                    filtered[key] = value
            elif key == "StartUTC":
                if isinstance(value, str) and re.fullmatch(r"[0-9TZ:.,+\-]{1,40}", value):
                    filtered[key] = value
            elif isinstance(value, (int, float)):
                filtered[key] = value
        if filtered:
            events.append(filtered)
    return events[-20:]


def grafana_client():

    return httpx.Client(
        base_url=f"https://localhost:{state()['https_port']}/grafana/",
        verify=ssl.create_default_context(cafile=str(PRIVATE / "ca.crt")),
        trust_env=False,
        timeout=20,
        auth=("admin", (PRIVATE / "grafana_password").read_text().strip()),
    )


def diagnose(name):
    report = {"time_utc": datetime.now(UTC).isoformat(), "vm": name}
    services = []
    result = command(name, "ps --all --format json", timeout=20, check=False)
    for line in result.stdout.decode().splitlines():
        try:
            row = json.loads(line)
            services.append(
                {key: row.get(key) for key in ("Service", "State", "Health", "ExitCode")}
            )
        except ValueError:
            continue
    report["services"] = services
    ids = []
    for line in result.stdout.decode().splitlines():
        try:
            container_id = json.loads(line).get("ID", "")
            if re.fullmatch(r"[a-f0-9]{12,64}", container_id):
                ids.append(container_id)
        except ValueError:
            pass
    if ids:
        template = (
            '{{index .Config.Labels "com.docker.compose.service"}}|'
            "{{.State.OOMKilled}}|{{.RestartCount}}|{{.Config.Image}}"
        )
        inspected = vm.ssh(
            name,
            "sudo docker inspect --format " + shlex.quote(template) + " " + " ".join(ids),
            check=False,
            timeout=20,
        )
        image_refs = {}
        for line in inspected.stdout.decode().splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue
            service, oom, restarts, reference = parts
            image_refs[service] = reference
            for row in services:
                if row["Service"] == service:
                    row.update(oom_killed=oom == "true", restarts=int(restarts))
        try:
            signed = registry.verify()
            mismatches = [
                service
                for service, ref in image_refs.items()
                if signed["images"]
                .get(
                    "api" if service == "worker" else service.replace("-exporter", "_exporter"), {}
                )
                .get("reference")
                != ref
            ]
            desired = json.loads((STATE / "deployment/compose.yaml").read_text())["services"]
            report["signed_release"] = {
                "verified": True,
                "full_runtime_security_passed": signed.get("full_runtime_security_passed", False),
                "running_image_mismatches": mismatches,
                "missing_services": sorted(set(desired) - image_refs.keys()),
            }
        except (OSError, ValueError, subprocess.CalledProcessError):
            report["signed_release"] = "verification unavailable"
    ping = command(name, "exec -T redis redis-cli ping", check=False, timeout=10)
    report["redis_ping"] = "OK" if ping.stdout.strip() == b"PONG" else "unavailable"
    health = {}
    try:
        with client(authenticated=False) as api:
            health = {path: api.get(path).status_code for path in ("/health/live", "/health/ready")}
        with client() as api:
            response = api.get("/api/collections")
            response.raise_for_status()
            counts = Counter()
            failures = Counter()
            for collection in response.json():
                response = api.get("/api/collections/" + collection["id"] + "/sources")
                response.raise_for_status()
                for row in response.json():
                    counts[row["status"]] += 1
                    if row.get("error_code"):
                        failures[row["error_code"]] += 1
            report["source_status_counts"] = dict(counts)
            report["source_error_counts"] = dict(failures)
    except httpx.HTTPError:
        report["api_inspection"] = "unavailable; inspect service health and proxy events"
    report["http_health"] = health
    logs = command(name, "logs --no-color --tail=100 proxy", timeout=20, check=False)
    access = command(
        name, "exec -T proxy tail -n 100 /var/log/traefik/access.jsonl", check=False, timeout=10
    )
    report["recent_proxy_events"] = proxy_events(
        logs.stdout.decode(errors="replace") + "\n" + access.stdout.decode(errors="replace")
    )
    try:
        with httpx.Client(trust_env=False, timeout=10) as metrics:
            values = {}
            for metric in (
                "up",
                "probe_success",
                "pg_up",
                "redis_up",
                "blueprint_automation_success",
                "time() - blueprint_automation_last_success",
                "platform_database_up",
                "platform_jobs",
                "platform_worker_heartbeat_age_seconds",
            ):
                response = metrics.get(
                    f"http://localhost:{state()['metrics_port']}/api/v1/query",
                    params={"query": metric},
                )
                response.raise_for_status()
                values[metric] = response.json()["data"]["result"]
            report["prometheus"] = values
    except (httpx.HTTPError, ValueError, KeyError):
        report["prometheus"] = "unavailable"
    try:
        with grafana_client() as grafana:
            results = {}
            for uid in ("prometheus", "loki", "tempo"):
                if uid == "tempo":
                    response = grafana.get("api/datasources/proxy/uid/tempo/ready")
                    results[uid] = {
                        "http_status": response.status_code,
                        "status": "OK" if response.status_code == 200 else "ERROR",
                        "checked_via": "Tempo readiness (plugin health RPC unsupported)",
                    }
                else:
                    response = grafana.get(f"api/datasources/uid/{uid}/health")
                    results[uid] = {
                        "http_status": response.status_code,
                        "status": response.json().get("status"),
                    }
            report["grafana_datasources"] = results
    except (OSError, httpx.HTTPError, ValueError, subprocess.CalledProcessError):
        report["grafana_datasources"] = "unavailable or not configured"
    try:
        rows = []
        for line in compose("ps", "--all", "--format", "json").splitlines():
            row = json.loads(line)
            if row.get("Service") in {"registry", "distribution", "alerts"}:
                rows.append({key: row.get(key) for key in ("Service", "State", "Health")})
        report["controller_registry_services"] = rows
        with httpx.Client(
            verify=ssl.create_default_context(cafile=str(PRIVATE / "ca.crt")),
            trust_env=False,
            auth=("lab", secret("registry-password").read_text()),
            timeout=10,
        ) as registry_api:
            report["registry_authenticated_tls"] = registry_api.get(
                f"https://localhost:{state()['registry_port']}/v2/"
            ).status_code
    except (OSError, httpx.HTTPError, ValueError, subprocess.CalledProcessError):
        report["controller_registry_services"] = "unavailable"
    save(STATE / "diagnostics/status.json", report)
    print(render_report(report))
    return report


def render_report(report):
    lines = [
        "Local platform: " + report["vm"],
        "",
        "SERVICE         STATE       HEALTH      RESTARTS  OOM",
    ]
    for row in report.get("services", []):
        lines.append(
            f"{row['Service']:<15} {row['State']:<11} {row.get('Health') or '-':<11} "
            f"{row.get('restarts', '?')!s:<9} {row.get('oom_killed', '?')}"
        )
    lines.extend(
        [
            "",
            "HTTP readiness: " + str(report.get("http_health", {})),
            "Redis ping: " + report.get("redis_ping", "unknown"),
            "Sources: " + str(report.get("source_status_counts", {})),
            "Source error codes: " + str(report.get("source_error_counts", {})),
        ]
    )
    metrics = report.get("prometheus", {})
    if isinstance(metrics, dict):
        targets = metrics.get("up", [])
        lines.append(
            "Metric targets: "
            + ", ".join(
                row["metric"].get("job", "unknown")
                + "="
                + ("UP" if row["value"][1] == "1" else "DOWN")
                for row in targets
            )
        )
        for metric, title, label in (
            ("probe_success", "HTTPS/body probes", "check"),
            ("blueprint_automation_success", "Scheduled checks", "check"),
        ):
            rows = metrics.get(metric, [])
            lines.append(
                title
                + ": "
                + (
                    ", ".join(
                        row["metric"].get(label, "unknown")
                        + "="
                        + ("PASS" if row["value"][1] == "1" else "FAIL")
                        for row in rows
                    )
                    if rows
                    else "metrics unavailable"
                )
            )
        rows = metrics.get("time() - blueprint_automation_last_success", [])
        lines.append(
            "Scheduled success age (s): "
            + (
                ", ".join(
                    row["metric"].get("check", "unknown") + "=" + str(round(float(row["value"][1])))
                    for row in rows
                )
                if rows
                else "metrics unavailable"
            )
        )
        age = metrics.get("platform_worker_heartbeat_age_seconds", [])
        if age:
            lines.append("Worker heartbeat age: " + str(round(float(age[0]["value"][1]), 2)) + " s")
    sources = report.get("grafana_datasources", {})
    lines.append(
        "Grafana datasources: "
        + (
            ", ".join(name + "=" + str(value.get("status")) for name, value in sources.items())
            if isinstance(sources, dict)
            else str(sources)
        )
    )
    lines.append("Signed running release: " + str(report.get("signed_release", "unverified")))
    lines.append(
        "Registry TLS/auth status: " + str(report.get("registry_authenticated_tls", "unknown"))
    )
    errors = [
        row
        for row in report.get("recent_proxy_events", [])
        if row.get("DownstreamStatus", 0) >= 400 or "reason" in row
    ]
    lines.append(
        "Recent proxy errors (history; compare current health): "
        + ("none in inspected tail" if not errors else "")
    )
    for row in errors[-5:]:
        lines.append("  " + str(row))
    lines.append("Full private report: .local/lab/diagnostics/status.json")
    return "\n".join(lines)
