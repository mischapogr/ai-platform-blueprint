"""Local registry TLS and authentication using the platform's pinned Traefik runtime."""

from .common import STATE, save


def configuration():
    routers = {}
    for name, rule, service, priority in (
        (
            "artifacts",
            "Path(`/lab-alerts`) || Path(`/operator-metrics`) || PathPrefix(`/backup-store/`)",
            "receiver",
            100,
        ),
        ("registry", "PathPrefix(`/`)", "distribution", 10),
    ):
        routers[name] = {
            "entryPoints": ["registry"],
            "rule": rule,
            "service": service,
            "priority": priority,
            "tls": {},
            "middlewares": ["owner-auth"],
        }
    routers["nexus"] = {
        "entryPoints": ["nexus"],
        "rule": "PathPrefix(`/`)",
        "service": "nexus",
        "tls": {},
    }
    return {
        "http": {
            "routers": routers,
            "middlewares": {
                "owner-auth": {
                    "forwardAuth": {
                        "address": "http://alerts:9099/auth",
                        "trustForwardHeader": False,
                        "authRequestHeaders": ["Authorization"],
                        "forwardBody": False,
                    }
                }
            },
            "services": {
                name: {"loadBalancer": {"servers": [{"url": url}]}}
                for name, url in (
                    ("receiver", "http://alerts:9099"),
                    ("distribution", "http://distribution:5000"),
                    ("nexus", "http://nexus:8081"),
                )
            },
        },
        "tls": {
            "stores": {
                "default": {
                    "defaultCertificate": {
                        "certFile": "/tls/server.crt",
                        "keyFile": "/tls/server.key",
                    }
                }
            }
        },
    }


def prepare(images):
    save(STATE / "controller.yaml", configuration(), 0o644)
    path = STATE / "compose.env"
    lines = [
        row
        for row in path.read_text().splitlines()
        if not row.startswith(
            ("LAB_CADDY_IMAGE=", "LAB_CONTROLLER_IMAGE=", "LAB_DISTRIBUTION_IMAGE=")
        )
    ]
    lines += [
        "LAB_CONTROLLER_IMAGE=" + images["proxy"]["reference"],
        "LAB_DISTRIBUTION_IMAGE=" + images["distribution"]["reference"],
    ]
    path.write_text("\n".join(lines) + "\n")
