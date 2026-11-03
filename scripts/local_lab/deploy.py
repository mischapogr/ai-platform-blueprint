"""Render and apply only a signature-verified local guest deployment."""

import json
import os
import shlex
import shutil
import subprocess

from . import registry, vm
from .common import PRIVATE, ROOT, STATE, run, save, secret, state

GUEST = "/opt/ai-platform-blueprint"


def inventory(name):
    cfg = state()
    folder = vm.directory(name)
    marker = json.loads((folder / "metadata.json").read_text())
    data = {
        "all": {
            "hosts": {
                "lab": {
                    "ansible_host": "127.0.0.1",
                    "ansible_port": marker["ssh_port"],
                    "ansible_user": "lab",
                    "ansible_ssh_private_key_file": str(PRIVATE / "lab_ssh"),
                    "ansible_ssh_common_args": (
                        "-F /dev/null -o IdentityAgent=none -o StrictHostKeyChecking=yes "
                        "-o UserKnownHostsFile=" + shlex.quote(str(folder / "known_hosts"))
                    ),
                }
            },
            "vars": {
                "project_root": str(ROOT),
                "lab_private": str(PRIVATE),
                "registry_port": cfg["registry_port"],
                "deployment_dir": str(STATE / "deployment"),
                "migrations": [
                    p.name for p in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql"))
                ],
            },
        }
    }
    target = folder / "inventory.json"
    save(target, data)
    return target


def ansible(name, tags=None):
    args = [
        str(ROOT / ".local/infra-venv/bin/ansible-playbook"),
        "-i",
        str(inventory(name)),
        str(ROOT / "infra/ansible/local-lab/playbook.yaml"),
    ]
    if tags:
        args.extend(["--tags", tags])
    env = {
        **os.environ,
        "ANSIBLE_LOCAL_TEMP": str(STATE / "ansible-tmp"),
        "ANSIBLE_SSH_CONTROL_PATH_DIR": str(STATE / "ansible-cp"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_NOCOWS": "1",
    }
    result = run(args, env=env, timeout=1800, check=False)
    log = vm.directory(name) / ("ansible-" + (tags or "deploy") + ".log")
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        print(result.stdout.decode(errors="replace")[-5000:])
        raise RuntimeError("Guest configuration failed; inspect " + str(log.relative_to(ROOT)))
    print("Ansible completed for " + name + (" (" + tags + ")" if tags else ""), flush=True)


def tunnel(name):
    cfg = state()
    folder = vm.directory(name)
    pid = folder / "registry-tunnel.pid"
    if pid.exists():
        old = int(pid.read_text())
        try:
            os.kill(old, 0)
            return
        except ProcessLookupError:
            pass
    args = [
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
        "-i",
        str(PRIVATE / "lab_ssh"),
        "-p",
        str(cfg["ssh_port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(folder / "known_hosts"),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=10",
        "-N",
        "-R",
        f"127.0.0.1:{cfg['registry_port']}:127.0.0.1:{cfg['registry_port']}",
        "lab@127.0.0.1",
    ]
    with (folder / "registry-tunnel.log").open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - explicit owned SSH tunnel argv
            args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )  # noqa: S603
    pid.write_text(str(process.pid))


def prepare():
    signed = registry.verify()
    cfg = state()
    target = STATE / "deployment"
    for name in ("secrets", "postgres", "migrations", "proxy", "monitoring", "trust"):
        (target / name).mkdir(parents=True, exist_ok=True)
    for name in ("db_password", "app_password", "workspace_token"):
        shutil.copyfile(secret(name), target / "secrets" / name)
        (target / "secrets" / name).chmod(0o644)
    (target / "secrets/openai_api_key").write_text("")
    shutil.copyfile(secret("registry-password"), target / "secrets/registry-password")
    (target / "secrets/registry-password").chmod(0o644)
    shutil.copyfile(ROOT / "infra/compose/postgres/init.sh", target / "postgres/init.sh")
    (target / "postgres/init.sh").chmod(0o755)
    for migration in (ROOT / "infra/compose/postgres").glob("[0-9]*.sql"):
        shutil.copyfile(migration, target / "migrations" / migration.name)
    shutil.copyfile(PRIVATE / "registry.crt", target / "proxy/tls.crt")
    shutil.copyfile(PRIVATE / "registry.key", target / "proxy/tls.key")
    shutil.copyfile(PRIVATE / "ca.crt", target / "trust/ca.crt")
    origin = f"https://localhost:{cfg['https_port']}"
    routes = {
        "http": {
            "routers": {
                "redirect": {
                    "entryPoints": ["web"],
                    "rule": "PathPrefix(`/`)",
                    "service": "api",
                    "middlewares": ["redirect"],
                },
                "workspace": {
                    "entryPoints": ["secure"],
                    "rule": (
                        "Host(`localhost`) && (Path(`/`) || PathPrefix(`/api/`) || "
                        "PathPrefix(`/assets/`) || PathPrefix(`/health/`) || "
                        "PathPrefix(`/runbooks/`))"
                    ),
                    "tls": {},
                    "service": "api",
                    "middlewares": ["limit", "headers", "rate"],
                },
                "upload": {
                    "entryPoints": ["secure"],
                    "rule": "Host(`localhost`) && PathRegexp(`^/api/collections/[^/]+/upload$`)",
                    "priority": 100,
                    "tls": {},
                    "service": "api",
                    "middlewares": ["upload-limit", "headers", "rate"],
                },
            },
            "services": {"api": {"loadBalancer": {"servers": [{"url": "http://api:8000"}]}}},
            "middlewares": {
                "redirect": {
                    "redirectScheme": {
                        "scheme": "https",
                        "port": str(cfg["https_port"]),
                        "permanent": True,
                    }
                },
                "limit": {"buffering": {"maxRequestBodyBytes": 65536}},
                "upload-limit": {"buffering": {"maxRequestBodyBytes": 11000000}},
                "rate": {"rateLimit": {"average": 10, "burst": 20}},
                "headers": {
                    "headers": {
                        "contentTypeNosniff": True,
                        "frameDeny": True,
                        "referrerPolicy": "no-referrer",
                    }
                },
            },
        },
        "tls": {"certificates": [{"certFile": "/tls/tls.crt", "keyFile": "/tls/tls.key"}]},
    }
    save(target / "proxy/routes.yaml", routes, 0o644)
    for path in ("db_password", "app_password", "workspace_token", "openai_api_key"):
        (target / "secrets" / path).chmod(0o644)
    images = {name: value["reference"] for name, value in signed["images"].items()}
    defaults = {
        "restart": "unless-stopped",
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
    }
    env = {
        "DB_HOST": "db",
        "DB_PASSWORD_FILE": "/run/secrets/app_password",
        "REDIS_HOST": "redis",
        "WORKSPACE_TOKEN_FILE": "/run/secrets/workspace_token",
        "OPENAI_API_KEY_FILE": "/run/secrets/openai_api_key",
        "WORKSPACE_ALLOWED_ORIGINS": origin,
        "WORKSPACE_COOKIE_SECURE": "1",
        "REQUEST_LOG": "/var/log/platform/requests.jsonl",
    }
    services = {
        "db": {
            **defaults,
            "image": images["db"],
            "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
            "environment": {
                "POSTGRES_DB": "platform",
                "POSTGRES_USER": "platform_admin",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/db_password",
            },
            "secrets": ["db_password", "app_password"],
            "volumes": [
                "database:/var/lib/postgresql/data",
                "./postgres/init.sh:/docker-entrypoint-initdb.d/10-init.sh:ro",
                "./migrations:/migrations:ro",
            ],
            "networks": ["backend"],
            "mem_limit": "512m",
            "cpus": 1,
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U platform_admin -d platform"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 20,
            },
        },
        "redis": {
            **defaults,
            "image": images["redis"],
            "user": "999:999",
            "read_only": True,
            "networks": ["backend"],
            "mem_limit": "64m",
            "cpus": 0.25,
            "command": [
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
                "--maxmemory",
                "32mb",
                "--maxmemory-policy",
                "allkeys-lru",
            ],
        },
        "api": {
            **defaults,
            "image": images["api"],
            "read_only": True,
            "tmpfs": ["/tmp:size=16m,mode=1777"],  # noqa: S108 - isolated container tmpfs
            "dns_opt": ["timeout:1", "attempts:1"],
            "environment": env,
            "secrets": ["app_password", "workspace_token", "openai_api_key"],
            "volumes": ["api_logs:/var/log/platform"],
            "networks": ["backend", "edge", "telemetry"],
            "mem_limit": "256m",
            "cpus": 1,
            "pids_limit": 128,
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request;urllib.request.urlopen('http://localhost:8000/health/ready',timeout=4)",
                ],
                "interval": "5s",
                "timeout": "5s",
                "retries": 20,
            },
        },
        "worker": {
            **defaults,
            "image": images["api"],
            "read_only": True,
            "tmpfs": ["/tmp:size=32m,mode=1777"],  # noqa: S108 - isolated container tmpfs
            "command": ["python", "/app/worker/main.py"],
            "environment": {**env, "PYTHONPATH": "/app"},
            "secrets": ["app_password", "openai_api_key"],
            "networks": ["backend", "egress"],
            "mem_limit": "768m",
            "cpus": 1,
            "pids_limit": 64,
        },
        "proxy": {
            **defaults,
            "image": images["proxy"],
            "user": "65532:65532",
            "read_only": True,
            "command": [
                "--entrypoints.web.address=:8080",
                "--entrypoints.secure.address=:8443",
                "--providers.file.filename=/etc/traefik/routes.yaml",
                "--log.level=WARN",
            ],
            "ports": ["8080:8080", "8443:8443"],
            "volumes": ["./proxy/routes.yaml:/etc/traefik/routes.yaml:ro", "./proxy:/tls:ro"],
            "networks": ["edge", "management"],
            "mem_limit": "128m",
            "cpus": 0.5,
        },
        "prometheus": {
            **defaults,
            "image": images["prometheus"],
            "read_only": True,
            "command": [
                "--config.file=/etc/prometheus/prometheus.yaml",
                "--storage.tsdb.path=/prometheus",
                "--storage.tsdb.retention.time=24h",
            ],
            "volumes": ["./monitoring:/etc/prometheus:ro", "prometheus:/prometheus"],
            "ports": ["9090:9090"],
            "networks": ["telemetry", "management"],
            "mem_limit": "256m",
            "cpus": 0.5,
        },
        "alertmanager": {
            **defaults,
            "image": images["alertmanager"],
            "read_only": True,
            "command": [
                "--config.file=/etc/monitoring/alertmanager.yaml",
                "--storage.path=/alertmanager",
            ],
            "volumes": [
                "./monitoring:/etc/monitoring:ro",
                "./trust:/trust:ro",
                "alertmanager:/alertmanager",
            ],
            "secrets": ["registry-password"],
            "networks": ["telemetry", "egress"],
            "mem_limit": "96m",
            "cpus": 0.25,
        },
        "node": {
            **defaults,
            "image": images["node"],
            "read_only": True,
            "pid": "host",
            "command": ["--path.rootfs=/host"],
            "volumes": ["/:/host:ro,rslave"],
            "networks": ["telemetry"],
            "mem_limit": "64m",
            "cpus": 0.25,
        },
    }
    config = {
        "services": services,
        "secrets": {
            name: {"file": "./secrets/" + name}
            for name in (
                *("db_password", "app_password", "workspace_token", "openai_api_key"),
                "registry-password",
            )
        },
        "volumes": {name: {} for name in ("database", "api_logs", "prometheus", "alertmanager")},
        "networks": {name: {"internal": True} for name in ("backend", "edge", "telemetry")},
    }
    config["networks"].update(egress={}, management={})
    save(target / "compose.yaml", config, 0o644)
    monitoring = {
        "global": {"scrape_interval": "5s", "evaluation_interval": "5s"},
        "rule_files": ["/etc/prometheus/alerts.yaml"],
        "alerting": {"alertmanagers": [{"static_configs": [{"targets": ["alertmanager:9093"]}]}]},
        "scrape_configs": [
            {
                "job_name": "api",
                "scrape_timeout": "4s",
                "static_configs": [{"targets": ["api:8000"]}],
            },
            {"job_name": "node", "static_configs": [{"targets": ["node:9100"]}]},
        ],
    }
    save(target / "monitoring/prometheus.yaml", monitoring, 0o644)
    alerts = {
        "groups": [
            {
                "name": "local-platform",
                "rules": [
                    {
                        "alert": "DatabaseUnavailable",
                        "expr": "platform_database_up == 0",
                        "for": "15s",
                        "labels": {"severity": "warning"},
                    },
                    {
                        "alert": "ApiUnavailable",
                        "expr": 'up{job="api"} == 0',
                        "for": "15s",
                        "labels": {"severity": "warning"},
                    },
                    {
                        "alert": "HostDiskLow",
                        "expr": (
                            'node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"} / '
                            'node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"} < 0.15'
                        ),
                        "for": "1m",
                        "labels": {"severity": "warning"},
                    },
                ],
            }
        ]
    }
    save(target / "monitoring/alerts.yaml", alerts, 0o644)
    save(
        target / "monitoring/alertmanager.yaml",
        {
            "route": {
                "receiver": "local-external",
                "group_wait": "1s",
                "group_interval": "5s",
                "repeat_interval": "1h",
            },
            "receivers": [
                {
                    "name": "local-external",
                    "webhook_configs": [
                        {
                            "url": f"https://10.0.2.2:{cfg['registry_port']}/lab-alerts",
                            "send_resolved": True,
                            "http_config": {
                                "tls_config": {"ca_file": "/trust/ca.crt"},
                                "basic_auth": {
                                    "username": "lab",
                                    "password_file": "/run/secrets/registry-password",
                                },
                            },
                        }
                    ],
                }
            ],
        },
        0o644,
    )
    return target


def apply(name):
    registry.verify()
    prepare()
    tunnel(name)
    ansible(name)
    print("Signed local guest deployment completed.", flush=True)
