"""Private Grafana/Loki/Tempo monitoring for the same guest as the workspace."""

import json
import shutil

from .common import ROOT, save, secret

OBSERVABILITY_IMAGES = ("grafana", "loki", "alloy", "tempo")
PROXY_LOG_FIELDS = (
    "StartUTC",
    "RequestMethod",
    "DownstreamStatus",
    "OriginStatus",
    "RouterName",
    "ServiceName",
    "Duration",
    "OriginDuration",
    "RetryAttempts",
)


def configure(target, config, routes, images, origin):
    missing = set(OBSERVABILITY_IMAGES) - images.keys()
    if missing:
        raise RuntimeError("Publish the monitoring images first: make lab-publish")
    for path in (
        "monitoring/grafana/datasources",
        "monitoring/grafana/dashboards",
        "monitoring/dashboards",
    ):
        (target / path).mkdir(parents=True, exist_ok=True)
    secret_path = target / "secrets/grafana_password"
    shutil.copyfile(secret("grafana_password"), secret_path)
    secret_path.chmod(0o644)
    config["secrets"]["grafana_password"] = {"file": "./secrets/grafana_password"}
    services = config["services"]
    for name in ("api", "worker"):
        services[name]["environment"]["PLATFORM_TRACE_ENDPOINT"] = "http://tempo:4318/v1/traces"
    if "telemetry" not in services["worker"]["networks"]:
        services["worker"]["networks"].append("telemetry")
    proxy = services["proxy"]
    proxy["command"].extend(
        [
            "--entrypoints.metrics.address=:9092",
            "--metrics.prometheus=true",
            "--metrics.prometheus.entrypoint=metrics",
            "--metrics.prometheus.addrouterslabels=true",
            "--accesslog=true",
            "--accesslog.format=json",
            "--accesslog.filepath=/var/log/traefik/access.jsonl",
            "--accesslog.fields.defaultmode=drop",
            "--accesslog.fields.headers.defaultmode=drop",
            *("--accesslog.fields.names." + name + "=keep" for name in PROXY_LOG_FIELDS),
        ]
    )
    proxy["volumes"].append("./proxy-logs:/var/log/traefik")
    proxy["networks"].append("telemetry")
    routes["http"]["routers"]["grafana"] = {
        "entryPoints": ["secure"],
        "rule": "Host(`localhost`) && (Path(`/grafana`) || PathPrefix(`/grafana/`))",
        "priority": 200,
        "tls": {},
        "service": "grafana",
        "middlewares": ["headers"],
    }
    routes["http"]["services"]["grafana"] = {
        "loadBalancer": {"servers": [{"url": "http://grafana:3000"}]}
    }
    base = {
        "restart": "unless-stopped",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "networks": ["telemetry"],
        "cpus": 0.5,
        "logging": {"driver": "json-file", "options": {"max-size": "5m", "max-file": "2"}},
    }
    services.update(
        {
            "loki": {
                **base,
                "image": images["loki"],
                "user": "10001:10001",
                "mem_limit": "384m",
                "command": ["-config.file=/etc/loki/config.yml"],
                "tmpfs": ["/tmp:size=16m,mode=1777"],  # noqa: S108 - isolated container tmpfs
                "volumes": ["./monitoring/loki.yml:/etc/loki/config.yml:ro", "loki:/loki"],
            },
            "tempo": {
                **base,
                "image": images["tempo"],
                "user": "10001:10001",
                "mem_limit": "384m",
                "command": ["-config.file=/etc/tempo.yml"],
                "volumes": ["./monitoring/tempo.yml:/etc/tempo.yml:ro", "./tempo-data:/tmp/tempo"],
            },
            "alloy": {
                **base,
                "image": images["alloy"],
                "user": "473:473",
                "mem_limit": "192m",
                "command": [
                    "run",
                    "--storage.path=/var/lib/alloy/data",
                    "--server.http.listen-addr=0.0.0.0:12345",
                    "/etc/alloy/config.alloy",
                ],
                "volumes": [
                    "./monitoring/config.alloy:/etc/alloy/config.alloy:ro",
                    "api_logs:/var/log/platform:ro",
                    "./proxy-logs:/var/log/proxy:ro",
                    "alloy:/var/lib/alloy",
                ],
            },
            "grafana": {
                **base,
                "image": images["grafana"],
                "user": "472:472",
                "mem_limit": "384m",
                "tmpfs": ["/tmp:size=32m,mode=1777"],  # noqa: S108 - isolated container tmpfs
                "secrets": ["grafana_password"],
                "environment": {
                    "GF_SECURITY_ADMIN_USER": "admin",
                    "GF_SECURITY_COOKIE_SECURE": "true",
                    "GF_SECURITY_COOKIE_SAMESITE": "strict",
                    "GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana_password",
                    "GF_SERVER_ROOT_URL": origin + "/grafana/",
                    "GF_SERVER_SERVE_FROM_SUB_PATH": "true",
                    "GF_USERS_ALLOW_SIGN_UP": "false",
                    "GF_AUTH_ANONYMOUS_ENABLED": "false",
                    "GF_PLUGINS_PREINSTALL_DISABLED": "true",
                    "GF_ANALYTICS_REPORTING_ENABLED": "false",
                    "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
                    "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                },
                "volumes": [
                    "./monitoring/grafana:/etc/grafana/provisioning:ro",
                    "./monitoring/dashboards:/etc/grafana/dashboards:ro",
                    "grafana:/var/lib/grafana",
                ],
            },
        }
    )
    config["volumes"].update({name: {} for name in (*OBSERVABILITY_IMAGES,)})
    prometheus_path = target / "monitoring/prometheus.yaml"
    prometheus = json.loads(prometheus_path.read_text())
    for name, port in (("proxy", 9092), ("loki", 3100), ("tempo", 3200), ("alloy", 12345)):
        prometheus["scrape_configs"].append(
            {"job_name": name, "static_configs": [{"targets": [f"{name}:{port}"]}]}
        )
    prometheus["scrape_configs"].append(
        {
            "job_name": "grafana",
            "metrics_path": "/grafana/metrics",
            "static_configs": [{"targets": ["grafana:3000"]}],
        }
    )
    for name, port in (("prometheus", 9090), ("alertmanager", 9093)):
        prometheus["scrape_configs"].append(
            {"job_name": name, "static_configs": [{"targets": [f"{name}:{port}"]}]}
        )
    save(prometheus_path, prometheus, 0o644)
    rules_path = target / "monitoring/alerts.yaml"
    rules = json.loads(rules_path.read_text())
    rules["groups"][0]["rules"].extend(
        [
            {
                "alert": "MonitoringTargetUnavailable",
                "expr": 'up{job=~"proxy|alloy|loki|tempo|grafana|node|alertmanager"} == 0',
                "for": "30s",
                "labels": {"severity": "warning"},
                "annotations": {
                    "summary": "A platform monitoring target is unavailable",
                    "runbook_url": origin + "/grafana/",
                },
            },
            {
                "alert": "KnowledgeWorkerUnavailable",
                "expr": "platform_worker_heartbeat_age_seconds > 210",
                "for": "30s",
                "labels": {"severity": "warning"},
                "annotations": {
                    "summary": "Knowledge worker heartbeat is stale",
                    "runbook_url": origin + "/runbooks/knowledge-ingestion",
                },
            },
        ]
    )
    save(rules_path, rules, 0o644)

    for component in ("loki", "tempo"):
        shutil.copyfile(
            ROOT / f"observability/{component}/{component}.yml",
            target / f"monitoring/{component}.yml",
        )
    shutil.copyfile(
        ROOT / "observability/grafana/provisioning/dashboards/provider.yml",
        target / "monitoring/grafana/dashboards/provider.yml",
    )
    dashboard = json.loads((ROOT / "observability/grafana/dashboards/platform.json").read_text())
    for link in dashboard.get("links", []):
        link["url"] = origin + "/runbooks/database-unavailable"
    # Put a human-readable first-stop view above the detailed existing charts.
    for panel in dashboard["panels"]:
        panel["gridPos"]["y"] += 21
    dashboard["panels"][0:0] = [
        {
            "id": 100,
            "title": "Start here",
            "type": "text",
            "gridPos": {"x": 0, "y": 0, "w": 24, "h": 6},
            "options": {
                "mode": "markdown",
                "content": (
                    "**Upload failed before appearing in Source library?** "
                    "Check Proxy errors below. "
                    "HTTP 413 means a request-size rejection; "
                    "OriginStatus 0 means the API was not reached.\n\n"
                    "**Source stays queued?** Check worker heartbeat and database health. "
                    "**Source is failed?** Read its error code in Source library, "
                    "then open API logs and "
                    "click the TraceID link to follow worker processing in Tempo.\n\n"
                    "[Open Explore](/grafana/explore) · "
                    "[Ingestion runbook](/runbooks/knowledge-ingestion)\n\n"
                    "Request path: browser → proxy → API → PostgreSQL queue "
                    "→ worker → parser/index. "
                    "Observability: API/proxy logs → Alloy → Loki; "
                    "API/worker spans → Tempo; metrics → Prometheus."
                ),
            },
        },
        {
            "id": 101,
            "title": "Telemetry targets (1 = scraping, 0 = investigate)",
            "type": "stat",
            "gridPos": {"x": 0, "y": 6, "w": 16, "h": 5},
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "targets": [{"refId": "A", "expr": "up", "instant": True, "legendFormat": "{{job}}"}],
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                "colorMode": "background",
                "textMode": "value_and_name",
            },
            "fieldConfig": {
                "defaults": {
                    "min": 0,
                    "max": 1,
                    "color": {"mode": "thresholds"},
                    "mappings": [
                        {"type": "value", "options": {"0": {"text": "DOWN"}, "1": {"text": "UP"}}}
                    ],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
                    },
                },
                "overrides": [],
            },
        },
        {
            "id": 102,
            "title": "Failed ingestion jobs (includes deliberate diagnostics)",
            "type": "stat",
            "gridPos": {"x": 16, "y": 6, "w": 8, "h": 5},
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "targets": [{"refId": "A", "expr": 'platform_jobs{status="failed"}', "instant": True}],
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                "colorMode": "background",
            },
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "orange", "value": 1},
                        ],
                    },
                },
                "overrides": [],
            },
        },
        {
            "id": 103,
            "title": "HTTP errors at proxy (including requests rejected before API)",
            "type": "timeseries",
            "gridPos": {"x": 0, "y": 11, "w": 24, "h": 5},
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "targets": [
                {
                    "refId": "A",
                    "expr": (
                        "sum by (code,router) (rate(traefik_router_requests_total"
                        '{code=~"4..|5.."}[5m]))'
                    ),
                    "legendFormat": "{{router}} {{code}}",
                }
            ],
        },
    ]
    for index, (title, expression, unit) in enumerate(
        [
            ("Guest available memory", "node_memory_MemAvailable_bytes", "bytes"),
            (
                "Guest available root disk",
                'node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"}',
                "bytes",
            ),
            (
                "Guest CPU busy",
                '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
                "percent",
            ),
        ]
    ):
        dashboard["panels"].append(
            {
                "id": 104 + index,
                "title": title,
                "type": "stat",
                "gridPos": {"x": 8 * index, "y": 16, "w": 8, "h": 5},
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "targets": [{"refId": "A", "expr": expression, "instant": True}],
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            }
        )
    logs_y = max(panel["gridPos"]["y"] + panel["gridPos"]["h"] for panel in dashboard["panels"])
    max_id = max(panel["id"] for panel in dashboard["panels"])
    for index, (title, expression) in enumerate(
        [
            ("Proxy errors (before/after API)", '{job="proxy"} | json | DownstreamStatus >= 400'),
            ("API requests (expand a row for its Tempo trace)", '{job="api"} | json'),
        ]
    ):
        dashboard["panels"].append(
            {
                "id": max_id + index + 1,
                "title": title,
                "type": "logs",
                "datasource": {"type": "loki", "uid": "loki"},
                "gridPos": {"x": 0, "y": logs_y + 10 * index, "w": 24, "h": 10},
                "targets": [{"refId": "A", "expr": expression}],
            }
        )
    save(target / "monitoring/dashboards/platform.json", dashboard, 0o644)
    datasources = {
        "apiVersion": 1,
        "datasources": [
            {
                "name": "Prometheus",
                "uid": "prometheus",
                "type": "prometheus",
                "access": "proxy",
                "url": "http://prometheus:9090",
                "isDefault": True,
                "editable": False,
            },
            {
                "name": "Loki",
                "uid": "loki",
                "type": "loki",
                "access": "proxy",
                "url": "http://loki:3100",
                "editable": False,
                "jsonData": {
                    "derivedFields": [
                        {
                            "name": "TraceID",
                            "matcherRegex": r'"trace_id":\s*"([a-f0-9]{32})"',
                            "datasourceUid": "tempo",
                            "url": "$${__value.raw}",
                        }
                    ]
                },
            },
            {
                "name": "Tempo",
                "uid": "tempo",
                "type": "tempo",
                "access": "proxy",
                "url": "http://tempo:3200",
                "editable": False,
                "jsonData": {
                    "tracesToLogsV2": {
                        "datasourceUid": "loki",
                        "spanStartTimeShift": "-1m",
                        "spanEndTimeShift": "1m",
                        "filterByTraceID": True,
                        "filterBySpanID": False,
                        "customQuery": True,
                        "query": '{job="api"} |= "$${__trace.traceId}"',
                    }
                },
            },
        ],
    }
    save(target / "monitoring/grafana/datasources/sources.yml", datasources, 0o644)
    (target / "monitoring/config.alloy").write_text("""local.file_match "requests" {
  path_targets = [
    {"__path__" = "/var/log/platform/requests.jsonl", "job" = "api"},
    {"__path__" = "/var/log/proxy/access.jsonl", "job" = "proxy"},
  ]
  sync_period = "5s"
}
loki.source.file "requests" {
  targets = local.file_match.requests.targets
  forward_to = [loki.write.local.receiver]
}
loki.write "local" {
  endpoint { url = "http://loki:3100/loki/api/v1/push" }
}
""")
    (
        target / "monitoring/proxy-logrotate.conf"
    ).write_text("""/opt/ai-platform-blueprint/proxy-logs/access.jsonl {
  size 2M
  rotate 2
  copytruncate
  missingok
  notifempty
}
""")
