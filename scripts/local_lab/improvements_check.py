"""Live acceptance for outside probes, restricted metrics, schedules and the alert inbox."""

import json
import shlex
from datetime import UTC, datetime

import httpx

from . import acceptance, vm
from .common import STATE, save, state, wait
from .diagnostics import grafana_client


def check(name):
    cfg = state()
    checks = {}
    with httpx.Client(trust_env=False, timeout=10) as api:

        def values(expression):
            response = api.get(
                f"http://localhost:{cfg['metrics_port']}/api/v1/query", params={"query": expression}
            )
            response.raise_for_status()
            return response.json()["data"]["result"]

        prometheus = json.loads((STATE / "deployment/monitoring/prometheus.yaml").read_text())
        expected = {row["job_name"] for row in prometheus["scrape_configs"]}

        def scrapes_ready():
            rows = values("up")
            return {row["metric"]["job"] for row in rows if row["value"][1] == "1"} == expected

        wait(scrapes_ready, seconds=60, message="Expected monitoring targets not all UP")
        checks["all_expected_scrapes_up"] = True
        for metric in ("pg_up", "redis_up"):
            checks[metric] = bool(values(metric)) and all(
                row["value"][1] == "1" for row in values(metric)
            )
        checks["outside_guest_positive_probes"] = len(values("probe_success == 1")) == 3
        checks["certificate_expiry_visible"] = bool(values("probe_ssl_earliest_cert_expiry"))
        for module, path, label in (
            ("wrong_hostname", "/health/ready", "invalid_tls_name_rejected"),
            ("ready", "/missing-synthetic-route", "bad_response_rejected"),
        ):
            response = api.get(
                f"http://127.0.0.1:{cfg['probe_port']}/probe",
                params={
                    "module": module,
                    "target": f"https://localhost:{cfg['https_port']}" + path,
                },
            )
            checks[label] = "probe_success 0\n" in response.text
        checks["resource_history_all_services"] = len(
            values("blueprint_container_memory_bytes > 0")
        ) == len(json.loads((STATE / "deployment/compose.yaml").read_text())["services"])
        checks["resource_sample_fresh"] = bool(
            values("time() - blueprint_resource_sampler_last_success_unixtime < 120")
        )
        checks["cpu_throttle_and_oom_counters"] = bool(
            values("blueprint_container_cpu_throttled_periods_total")
        ) and bool(values("blueprint_container_oom_kills_total"))
        checks["scheduled_jobs_recent_success"] = (
            len(values("blueprint_automation_success == 1")) == 3
            and len(values('time() - blueprint_automation_last_success{check="workflow"} < 1800'))
            == 1
            and len(
                values(
                    'time() - blueprint_automation_last_success{check=~"backup|restore"} < 93600'
                )
            )
            == 2
        )
        with grafana_client() as grafana:
            response = grafana.get("api/datasources/proxy/uid/alertmanager/api/v2/status")
            checks["grafana_alert_inbox_connected"] = response.status_code == 200
        # A read-only monitor must not gain access to application tables.
        result = acceptance.command(
            name,
            "exec -T db psql -U blueprint_metrics -d platform "
            "-v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM kb_sources'",
            check=False,
        )
        checks["db_monitor_cannot_read_knowledge"] = (
            result.returncode != 0 and b"permission denied" in result.stderr
        )
        code = """import json,subprocess
from pathlib import Path
password=Path('/opt/ai-platform-blueprint/secrets/redis_metrics_password').read_bytes().strip()+b'\\n'
base=['docker','compose','--project-name','blueprint-local','--file','/opt/ai-platform-blueprint/compose.yaml',
      'exec','-T','redis','redis-cli','--user','blueprint_metrics','--askpass']
r=subprocess.run(base+['GET','kb:heartbeat'],input=password,capture_output=True)
print('DENIED' if b'NOPERM' in r.stdout+r.stderr else 'FAIL')
"""
        result = vm.ssh(name, "sudo python3 -c " + shlex.quote(code))
        checks["redis_monitor_cannot_read_keys"] = result.stdout.strip() == b"DENIED"
        # Exporter metrics must contain aggregate statistics, never query/key payloads.
        code = """import subprocess,urllib.request
for service,port in [('postgres-exporter',9187),('redis-exporter',9121)]:
 cid=subprocess.check_output(['docker','compose','--project-name','blueprint-local','--file',
 '/opt/ai-platform-blueprint/compose.yaml','ps','-q',service]).decode().strip()
 fmt='{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'
 info=subprocess.check_output(['docker','inspect','--format',fmt,cid]).decode().split()[0]
 data=urllib.request.urlopen('http://'+info+':'+str(port)+'/metrics',timeout=10).read()
 forbidden=[b'query="',b'key="',b'QuartzBeacon',b'kb:heartbeat']
 print(service+':'+str(not any(token in data for token in forbidden)))
"""
        result = vm.ssh(name, "sudo python3 -c " + shlex.quote(code))
        checks["exporters_exclude_sql_and_key_values"] = result.stdout.count(b":True") == 2
        timers = vm.ssh(name, "sudo systemctl is-active blueprint-resources.timer")
        checks["guest_resource_timer_active"] = timers.stdout.strip() == b"active"
    events = STATE / "alerts/events.jsonl"
    offset = events.stat().st_size if events.exists() else 0

    def received(status):
        if not events.exists():
            return False
        with events.open() as stream:
            stream.seek(offset)
            return any(
                row.get("alert") == "RedisMetricsUnavailable" and row.get("status") == status
                for row in (json.loads(line) for line in stream)
            )

    acceptance.command(name, "stop redis-exporter")
    try:
        with acceptance.client(authenticated=False) as api:
            checks["app_ready_during_exporter_outage"] = api.get("/health/ready").status_code == 200
        wait(
            lambda: received("firing"),
            seconds=90,
            message="Exporter outage notification not received",
        )
        with grafana_client() as grafana:
            response = grafana.get("api/datasources/proxy/uid/alertmanager/api/v2/alerts")
            response.raise_for_status()
            checks["firing_visible_in_grafana_inbox"] = any(
                row["labels"].get("alertname") == "RedisMetricsUnavailable"
                for row in response.json()
            )
    finally:
        acceptance.command(name, "up --detach redis-exporter")
    wait(
        lambda: received("resolved"),
        seconds=90,
        message="Exporter resolution notification not received",
    )
    checks["local_alert_firing_and_resolution"] = True
    save(
        STATE / "improvements-receipt.json",
        {
            "time_utc": datetime.now(UTC).isoformat(),
            "vm": name,
            "checks": checks,
            "scope": "local-only; no human paging or physical offsite",
        },
    )
    failed = [key for key, value in checks.items() if not value]
    if failed:
        raise AssertionError("Improvement checks failed: " + ", ".join(failed))
    print(f"Local monitoring improvements: {len(checks)} checks passed.")
