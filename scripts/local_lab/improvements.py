"""Aggregate-only exporters and outside-guest TLS probes for the local lab."""

import hashlib
import json
import shutil

from .common import ROOT, STATE, compose, image_lock, save, secret, state, wait

EXPORTER_IMAGES = ("postgres_exporter", "redis_exporter")


def prober_up():
    cfg = state()
    images = image_lock()
    modules = {}
    for name, expression in (
        ("workspace", "(?i)knowledge"),
        ("ready", r'"status"\s*:\s*"ready"'),
        ("grafana", "Grafana"),
    ):
        modules[name] = {
            "prober": "http",
            "timeout": "4s",
            "http": {
                "preferred_ip_protocol": "ip4",
                "ip_protocol_fallback": False,
                "valid_status_codes": [200],
                "follow_redirects": False,
                "fail_if_not_ssl": True,
                "headers": {"Host": "localhost"},
                "body_size_limit": "1MiB",
                "fail_if_body_not_matches_regexp": [expression],
                "tls_config": {
                    "ca_file": "/trust/ca.crt",
                    "server_name": "localhost",
                    "insecure_skip_verify": False,
                },
            },
        }
    modules["wrong_hostname"] = json.loads(json.dumps(modules["ready"]))
    modules["wrong_hostname"]["http"]["tls_config"]["server_name"] = "wrong.invalid"
    modules["wrong_hostname"]["http"]["headers"]["Host"] = "wrong.invalid"
    save(STATE / "blackbox.yml", {"modules": modules}, 0o644)
    env_path = STATE / "compose.env"
    rows = [row for row in env_path.read_text().splitlines() if not row.startswith("LAB_BLACKBOX_")]
    rows += [
        "LAB_BLACKBOX_IMAGE=" + images["blackbox"]["reference"],
        "LAB_BLACKBOX_PORT=" + str(cfg["probe_port"]),
    ]
    env_path.write_text("\n".join(rows) + "\n")
    compose("up", "--detach", "blackbox")
    import httpx

    with httpx.Client(trust_env=False, timeout=5) as client:
        wait(
            lambda: (
                client.get(f"http://127.0.0.1:{cfg['probe_port']}/-/healthy").status_code == 200
            ),
            seconds=30,
        )
    print("Outside-guest TLS prober is ready on its project-owned loopback port.")


def configure(target, config, images, origin):
    services = config["services"]
    if not set(EXPORTER_IMAGES) <= images.keys():
        raise RuntimeError("Publish the two exporter images first")
    for name in ("postgres_metrics_password", "redis_metrics_password"):
        shutil.copyfile(secret(name), target / "secrets" / name)
        (target / "secrets" / name).chmod(0o644)
        config["secrets"][name] = {"file": "./secrets/" + name}
    services["db"]["secrets"].append("postgres_metrics_password")
    services["db"]["volumes"].append("./monitoring:/monitoring:ro")
    (target / "monitoring/postgres-metrics-role.sql").write_text("""\\set ON_ERROR_STOP on
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='blueprint_metrics') THEN
   CREATE ROLE blueprint_metrics LOGIN;
 END IF;
END $$;
SELECT format('ALTER ROLE blueprint_metrics LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
 || 'NOREPLICATION INHERIT CONNECTION LIMIT 3 PASSWORD %L',
 btrim(pg_read_file('/run/secrets/postgres_metrics_password'))) \\gexec
GRANT pg_monitor TO blueprint_metrics;
GRANT CONNECT ON DATABASE platform TO blueprint_metrics;
ALTER ROLE blueprint_metrics SET statement_timeout='3s';
ALTER ROLE blueprint_metrics SET lock_timeout='1s';
ALTER ROLE blueprint_metrics SET default_transaction_read_only=on;
""")
    # The default worker account is preserved. The monitor cannot read any keys.
    password_hash = hashlib.sha256(
        secret("redis_metrics_password").read_bytes().strip()
    ).hexdigest()
    (target / "monitoring/redis.acl").write_text(
        "user default on nopass ~* &* +@all\n"
        + "user blueprint_metrics on #"
        + password_hash
        + " resetkeys resetchannels -@all +ping +info +client|setname +client|setinfo\n"
    )
    services["redis"]["command"] += ["--aclfile", "/etc/redis/metrics.acl"]
    services["redis"].setdefault("volumes", []).append(
        "./monitoring/redis.acl:/etc/redis/metrics.acl:ro"
    )
    base = {
        "restart": "unless-stopped",
        "read_only": True,
        "user": "65534:65534",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "networks": ["backend", "telemetry"],
        "mem_limit": "96m",
        "cpus": 0.25,
        "logging": {"driver": "json-file", "options": {"max-size": "2m", "max-file": "2"}},
    }
    services["postgres-exporter"] = {
        **base,
        "image": images["postgres_exporter"],
        "environment": {
            "DATA_SOURCE_URI": "db:5432/platform?sslmode=disable",
            "DATA_SOURCE_USER": "blueprint_metrics",
            "DATA_SOURCE_PASS_FILE": "/run/secrets/postgres_metrics_password",
            "PG_EXPORTER_COLLECTION_TIMEOUT": "3s",
        },
        "secrets": ["postgres_metrics_password"],
        "command": [
            "--no-collector.stat_statements",
            "--no-collector.stat_statements.include_query",
            "--no-collector.settings",
            "--no-collector.stat_user_tables",
            "--no-collector.statio_user_tables",
            "--no-collector.replication",
            "--no-collector.replication_slots",
            "--no-collector.stat_replication",
            "--no-collector.stat_progress_vacuum",
        ],
    }
    save(
        target / "secrets/redis_metrics_passwords",
        {"redis://redis:6379": secret("redis_metrics_password").read_text().strip()},
        0o644,
    )
    config["secrets"]["redis_metrics_passwords"] = {"file": "./secrets/redis_metrics_passwords"}
    services["redis-exporter"] = {
        **base,
        "image": images["redis_exporter"],
        "secrets": ["redis_metrics_passwords"],
        "command": [
            "--redis.addr=redis://redis:6379",
            "--redis.user=blueprint_metrics",
            "--redis.password-file=/run/secrets/redis_metrics_passwords",
            "--config-command=-",
            "--disable-exporting-key-values=true",
            "--exclude-slowlog-metrics=true",
            "--exclude-commandlog-metrics=true",
            "--exclude-latency-histogram-metrics=true",
            "--ping-on-connect=true",
        ],
    }
    shutil.copyfile(
        ROOT / "scripts/local_lab/guest_resources.py", target / "monitoring/resource_sampler.py"
    )
    services["node"]["command"].append(
        "--collector.textfile.directory=/var/lib/node_exporter/textfile"
    )
    services["node"]["volumes"].append("./node-textfile:/var/lib/node_exporter/textfile:ro")
    # Prometheus already has management egress; only static local targets are configured.
    prometheus_path = target / "monitoring/prometheus.yaml"
    prometheus = json.loads(prometheus_path.read_text())
    for name, port in (("postgres-exporter", 9187), ("redis-exporter", 9121)):
        prometheus["scrape_configs"].append(
            {"job_name": name, "static_configs": [{"targets": [f"{name}:{port}"]}]}
        )
    cfg = state()
    for module, path in (
        ("workspace", "/"),
        ("ready", "/health/ready"),
        ("grafana", "/grafana/login"),
    ):
        prometheus["scrape_configs"].append(
            {
                "job_name": "external-" + module,
                "metrics_path": "/probe",
                "scrape_interval": "15s",
                "scrape_timeout": "5s",
                "params": {"module": [module]},
                "static_configs": [{"targets": [origin + path], "labels": {"check": module}}],
                "relabel_configs": [
                    {"source_labels": ["__address__"], "target_label": "__param_target"},
                    {"source_labels": ["__param_target"], "target_label": "instance"},
                    {"target_label": "__address__", "replacement": f"10.0.2.2:{cfg['probe_port']}"},
                ],
            }
        )
    prometheus["scrape_configs"].append(
        {
            "job_name": "external-prober",
            "static_configs": [{"targets": [f"10.0.2.2:{cfg['probe_port']}"]}],
        }
    )
    services["prometheus"].setdefault("secrets", []).append("registry-password")
    services["prometheus"]["volumes"].append("./trust:/trust:ro")
    prometheus["scrape_configs"].append(
        {
            "job_name": "operator-checks",
            "scheme": "https",
            "metrics_path": "/operator-metrics",
            "static_configs": [{"targets": [f"10.0.2.2:{cfg['registry_port']}"]}],
            "basic_auth": {"username": "lab", "password_file": "/run/secrets/registry-password"},
            "tls_config": {"ca_file": "/trust/ca.crt", "server_name": "localhost"},
        }
    )
    save(prometheus_path, prometheus, 0o644)
    rules_path = target / "monitoring/alerts.yaml"
    rules = json.loads(rules_path.read_text())
    rules["groups"][0]["rules"].extend(
        [
            {
                "alert": "ExternalEndpointUnavailable",
                "expr": (
                    'probe_success{job=~"external-.*"} == 0 or up{job=~"external-.*"} == 0 '
                    'or absent(probe_success{job="external-ready"}) '
                    'or absent(probe_success{job="external-workspace"}) '
                    'or absent(probe_success{job="external-grafana"})'
                ),
                "for": "30s",
                "labels": {"severity": "critical"},
                "annotations": {
                    "summary": "The user-facing HTTPS path failed an independent probe"
                },
            },
            {
                "alert": "CertificateExpiring",
                "expr": "probe_ssl_earliest_cert_expiry - time() < 604800",
                "for": "1m",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "The local TLS certificate expires within seven days"},
            },
            {
                "alert": "DatabaseMetricsUnavailable",
                "expr": 'pg_up == 0 or absent(pg_up) or up{job="postgres-exporter"} == 0',
                "for": "30s",
                "labels": {"severity": "warning"},
            },
            {
                "alert": "RedisMetricsUnavailable",
                "expr": 'redis_up == 0 or absent(redis_up) or up{job="redis-exporter"} == 0',
                "for": "30s",
                "labels": {"severity": "warning"},
            },
        ]
    )
    rules["groups"][0]["rules"].append(
        {
            "alert": "ResourceSamplerStale",
            "expr": (
                "time() - blueprint_resource_sampler_last_success_unixtime > 180 "
                "or absent(blueprint_resource_sampler_last_success_unixtime)"
            ),
            "for": "1m",
            "labels": {"severity": "warning"},
        }
    )
    for check, limit in (("workflow", 1800), ("backup", 93600), ("restore", 93600)):
        rules["groups"][0]["rules"].append(
            {
                "alert": check.capitalize() + "Stale",
                "expr": f'time() - blueprint_automation_last_success{{check="{check}"}} > {limit} '
                f'or absent(blueprint_automation_last_success{{check="{check}"}})',
                "for": "1m",
                "labels": {"severity": "warning", "component": check},
                "annotations": {"summary": f"Last successful {check} check is overdue"},
            }
        )
    rules["groups"][0]["rules"].extend(
        [
            {
                "alert": "AutomationFailed",
                "expr": "blueprint_automation_success == 0",
                "for": "1m",
                "labels": {"severity": "warning"},
                "annotations": {"summary": "A local workflow, backup or restore check failed"},
            },
            {
                "alert": "AutomationMetricsUnavailable",
                "expr": 'up{job="operator-checks"} == 0',
                "for": "30s",
                "labels": {"severity": "warning"},
            },
        ]
    )
    for rule in rules["groups"][0]["rules"]:
        annotations = rule.setdefault("annotations", {})
        summaries = {
            "DatabaseMetricsUnavailable": "Database metrics failed; check exporter connection/role",
            "RedisMetricsUnavailable": "Redis metrics failed; check exporter connection/ACL",
            "ResourceSamplerStale": "Resource history is stale; check guest sampler timer",
        }
        annotations.setdefault("summary", summaries.get(rule["alert"], rule["alert"]))
        annotations.setdefault("runbook_url", origin + "/grafana/d/platform-overview")
    save(rules_path, rules, 0o644)
    dashboard_path = target / "monitoring/dashboards/platform.json"
    dashboard = json.loads(dashboard_path.read_text())
    dashboard["links"] = [
        {
            "title": "Local alert inbox",
            "type": "link",
            "url": origin + "/grafana/alerting/list",
            "targetBlank": False,
        }
    ]
    y = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in dashboard["panels"])
    queries = [
        ("Scheduled check success", "blueprint_automation_success", "short"),
        ("Age of last successful check", "time() - blueprint_automation_last_success", "s"),
        ("Outside-guest HTTPS checks", 'probe_success{job=~"external-.*"}', "short"),
        ("TLS certificate remaining", "probe_ssl_earliest_cert_expiry - time()", "s"),
        ("PostgreSQL connections", 'pg_stat_database_numbackends{datname="platform"}', "short"),
        ("PostgreSQL locks", "sum by(mode) (pg_locks_count)", "short"),
        ("Redis memory", "redis_memory_used_bytes", "bytes"),
        ("Redis ping duration", "redis_last_ping_time_seconds", "s"),
        ("Container CPU by service", "blueprint_container_cpu_percent", "percent"),
        ("Container memory by service", "blueprint_container_memory_bytes", "bytes"),
        (
            "Container CPU throttling",
            "rate(blueprint_container_cpu_throttled_periods_total[5m])",
            "short",
        ),
        ("Container OOM events", "blueprint_container_oom_kills_total", "short"),
    ]
    for index, (title, expression, unit) in enumerate(queries):
        dashboard["panels"].append(
            {
                "id": 200 + index,
                "title": title,
                "type": "timeseries",
                "gridPos": {"x": 12 * (index % 2), "y": y + 8 * (index // 2), "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [{"refId": "A", "expr": expression}],
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            }
        )
    save(dashboard_path, dashboard, 0o644)
    sources_path = target / "monitoring/grafana/datasources/sources.yml"
    sources = json.loads(sources_path.read_text())
    sources["datasources"].append(
        {
            "name": "Local alerts",
            "uid": "alertmanager",
            "type": "alertmanager",
            "access": "proxy",
            "url": "http://alertmanager:9093",
            "editable": False,
            "jsonData": {"implementation": "prometheus", "handleGrafanaManagedAlerts": False},
        }
    )
    save(sources_path, sources, 0o644)
