"""Project-scoped local lab paths, subprocesses and private configuration."""

import hashlib
import json
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".local/lab"
PRIVATE = ROOT / ".secrets/local-lab"
BIN = ROOT / ".local/lab/bin"
DOCKER = shutil.which("docker") or "/usr/bin/docker"


def run(args, *, input=None, timeout=600, env=None, check=True):
    return subprocess.run(  # noqa: S603 - explicit controlled argv; no shell
        args,
        cwd=ROOT,
        input=input,
        capture_output=True,
        check=check,  # noqa: S603
        timeout=timeout,
        env=env,
    )


def docker(*args, timeout=600):
    return run([DOCKER, *args], timeout=timeout).stdout.decode().strip()


def save(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(mode)


def secret(name, value=None):
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = PRIVATE / name
    if not path.exists():
        path.write_text(value if value is not None else secrets.token_hex(32))
        path.chmod(0o600)
    return path


def free_port(preferred):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def state():
    path = STATE / "state.json"
    if path.exists():
        return json.loads(path.read_text())
    data = {
        "schema": 1,
        "id": uuid.uuid4().hex[:10],
        "nexus_port": free_port(18081),
        "registry_port": free_port(15443),
        "http_port": free_port(18080),
        "https_port": free_port(18443),
        "ssh_port": free_port(16222),
    }
    data["project"] = "ai-blueprint-lab-" + data["id"]
    save(path, data)
    return data


def wait(check, *, seconds=300, message="Service did not become ready"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, httpx.HTTPError, subprocess.CalledProcessError):
            pass
        time.sleep(2)
    raise RuntimeError(message)


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, target, expected=None):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() and (expected is None or checksum(target) == expected):
        return
    part = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with part.open("wb") as stream:
            for chunk in response.iter_bytes():
                stream.write(chunk)
    if expected and checksum(part) != expected:
        raise ValueError("Downloaded artifact checksum mismatch")
    part.replace(target)


def compose(*args):
    cfg = state()
    return docker(
        "compose",
        "--project-name",
        cfg["project"],
        "--env-file",
        str(STATE / "compose.env"),
        "-f",
        str(ROOT / "infra/local-lab/compose.yaml"),
        *args,
    )


def image_lock():
    path = ROOT / "infra/local-lab/images.lock.json"
    if path.exists():
        return json.loads(path.read_text())
    sources = {
        "nexus": "sonatype/nexus3:latest",
        "skopeo": "quay.io/skopeo/stable:latest",
        "caddy": "caddy:2.11.4",
        "db": "pgvector/pgvector:0.8.1-pg16-bookworm",
        "redis": "redis:7.4.2-alpine",
        "proxy": "traefik:v3.6.1",
        "prometheus": "prom/prometheus:v3.7.3",
        "alertmanager": "prom/alertmanager:v0.29.0",
        "node": "prom/node-exporter:v1.9.1",
    }
    images = {}
    for name, ref in sources.items():
        try:
            info = json.loads(docker("image", "inspect", ref))[0]
        except subprocess.CalledProcessError:
            docker("pull", ref)
            info = json.loads(docker("image", "inspect", ref))[0]
        if not info.get("RepoDigests"):
            raise RuntimeError("Runtime image has no immutable repository digest: " + name)
        images[name] = {
            "source": ref,
            "reference": info["RepoDigests"][0],
            "config_digest": info["Id"],
        }
    save(path, images, 0o644)
    return images
