"""Local registry authentication, encrypted backup store and bounded alert receiver."""

import base64
import binascii
import hmac
import json
import math
import os
import re
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_ARCHIVE = 128 * 1024 * 1024


AUTOMATION_DIR = Path("/automation")
JOBS = ("workflow", "backup", "restore")


def operator_metrics():
    lines = []
    for job in JOBS:
        path = AUTOMATION_DIR / (job + ".json")
        try:
            record = json.loads(path.read_text())
            for field in ("last_run", "last_success", "duration_seconds"):
                value = record.get(field, 0)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid numeric receipt")
                lines.append(f'blueprint_automation_{field}{{check="{job}"}} {value}')
            success = int(record.get("success") is True)
            lines.append(f'blueprint_automation_success{{check="{job}"}} {success}')
        except (OSError, ValueError, TypeError):
            lines.append(f'blueprint_automation_success{{check="{job}"}} 0')
            lines.append(f'blueprint_automation_last_success{{check="{job}"}} 0')
    return "\n".join(lines) + "\n"


class Server(BaseHTTPRequestHandler):
    def authorized(self):
        header = self.headers.get("Authorization", "")
        try:
            supplied = base64.b64decode(header.removeprefix("Basic "), validate=True)
        except (ValueError, binascii.Error):
            return False
        expected = b"lab:" + Path("/run/registry-password").read_bytes().strip()
        return header.startswith("Basic ") and hmac.compare_digest(supplied, expected)

    def respond(self, status):
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Blueprint local registry"')
        self.end_headers()

    def archive(self):
        name = urlsplit(self.path).path.removeprefix("/backup-store/")
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,100}\.fernet", name):
            return None
        target = Path("/backups") / name
        return None if target.is_symlink() else target

    def do_GET(self):
        if not self.authorized():
            self.respond(401)
            return
        if urlsplit(self.path).path == "/auth":
            self.respond(200)
            return
        if urlsplit(self.path).path == "/operator-metrics":
            body = operator_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        target = self.archive()
        if target is None or not target.is_file() or target.stat().st_size > MAX_ARCHIVE:
            self.respond(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_PUT(self):
        if not self.authorized():
            self.respond(401)
            return
        target = self.archive()
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            remaining = 0
        if target is None or not 0 < remaining <= MAX_ARCHIVE:
            self.respond(400)
            return
        if target.exists():
            self.respond(409)
            return
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
        try:
            with temporary.open("xb") as stream:
                while remaining:
                    block = self.rfile.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError("Truncated encrypted archive")
                    stream.write(block)
                    remaining -= len(block)
            os.link(temporary, target)
            temporary.unlink()
            self.respond(201)
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            self.respond(400)

    def do_DELETE(self):
        if not self.authorized():
            self.respond(401)
            return
        target = self.archive()
        if target is None:
            self.respond(400)
            return
        target.unlink(missing_ok=True)
        self.respond(204)

    def do_POST(self):
        if not self.authorized():
            self.respond(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                self.respond(413)
                return
            data = json.loads(self.rfile.read(length))
            with Path("/data/events.jsonl").open("a") as stream:
                for alert in data.get("alerts", [])[:20]:
                    name = alert.get("labels", {}).get("alertname")
                    if name in {
                        "ModelRuntimeUnavailable",
                        "ExternalEndpointUnavailable",
                        "CertificateExpiring",
                        "DatabaseMetricsUnavailable",
                        "RedisMetricsUnavailable",
                        "ResourceSamplerStale",
                        "WorkflowStale",
                        "BackupStale",
                        "RestoreStale",
                        "AutomationFailed",
                        "AutomationMetricsUnavailable",
                        "DatabaseUnavailable",
                        "ApiUnavailable",
                        "HostDiskLow",
                        "MonitoringTargetUnavailable",
                        "KnowledgeWorkerUnavailable",
                    }:
                        stream.write(
                            json.dumps(
                                {
                                    "time_utc": datetime.now(UTC).isoformat(),
                                    "alert": name,
                                    "component": alert.get("labels", {}).get("job")
                                    if alert.get("labels", {}).get("job")
                                    in {
                                        "external-workspace",
                                        "external-ready",
                                        "external-grafana",
                                        "postgres-exporter",
                                        "redis-exporter",
                                        "operator-checks",
                                        "ollama",
                                        "api",
                                        "proxy",
                                        "loki",
                                        "tempo",
                                        "alloy",
                                        "grafana",
                                        "node",
                                        "prometheus",
                                        "alertmanager",
                                    }
                                    else "platform",
                                    "status": "resolved"
                                    if alert.get("status") == "resolved"
                                    else "firing",
                                }
                            )
                            + "\n"
                        )
            self.respond(204)
        except (ValueError, TypeError, AttributeError):
            self.respond(400)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9099), Server).serve_forever()  # noqa: S104
