"""Bring up an owner-scoped OCI registry and local signing material."""

import json
import os
import re
import subprocess

from .common import (
    BIN,
    PRIVATE,
    ROOT,
    STATE,
    docker,
    download,
    image_lock,
    run,
    secret,
    state,
)


def tls():
    if (PRIVATE / "registry.crt").exists():
        return
    openssl = "/usr/bin/openssl"
    run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-keyout",
            str(PRIVATE / "ca.key"),
            "-out",
            str(PRIVATE / "ca.crt"),
            "-days",
            "365",
            "-subj",
            "/CN=AI-Blueprint-Local-Lab-CA",
        ]
    )
    run(
        [
            openssl,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(PRIVATE / "registry.key"),
            "-out",
            str(PRIVATE / "registry.csr"),
            "-subj",
            "/CN=localhost",
        ]
    )
    extension = STATE / "certificate.ext"
    extension.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.0.2.2\n")
    run(
        [
            openssl,
            "x509",
            "-req",
            "-in",
            str(PRIVATE / "registry.csr"),
            "-CA",
            str(PRIVATE / "ca.crt"),
            "-CAkey",
            str(PRIVATE / "ca.key"),
            "-CAcreateserial",
            "-out",
            str(PRIVATE / "registry.crt"),
            "-days",
            "30",
            "-extfile",
            str(extension),
        ]
    )
    for path in PRIVATE.glob("*"):
        if path.suffix in {".key", ".csr"}:
            path.chmod(0o600)
    (PRIVATE / "registry.crt").chmod(0o644)
    (PRIVATE / "ca.crt").chmod(0o644)


def cosign():
    BIN.mkdir(parents=True, exist_ok=True)
    version = "v3.1.4"
    base = f"https://github.com/sigstore/cosign/releases/download/{version}/"
    checks = BIN / "cosign_checksums.txt"
    download(base + "cosign_checksums.txt", checks)
    match = re.search(r"([a-f0-9]{64})\s+\*?cosign-linux-amd64(?:\s|$)", checks.read_text())
    if not match:
        raise ValueError("Cosign vendor checksum is unavailable")
    binary = BIN / "cosign"
    download(base + "cosign-linux-amd64", binary, match[1])
    binary.chmod(0o755)
    password = secret("signing-password").read_text()
    if not (PRIVATE / "cosign.key").exists():
        run(
            [str(binary), "generate-key-pair", "--output-key-prefix", str(PRIVATE / "cosign")],
            env={**os.environ, "COSIGN_PASSWORD": password},
        )
        (PRIVATE / "cosign.key").chmod(0o600)
    return binary


def ensure_image(name, locked):
    try:
        info = json.loads(docker("image", "inspect", locked["reference"]))[0]
    except subprocess.CalledProcessError:
        if (ROOT / f"infra/local-lab/{name.replace('_', '-')}-source.lock.json").exists():
            stem = name.replace("_", "-")
            source = json.loads((ROOT / f"infra/local-lab/{stem}-source.lock.json").read_text())
            arguments = []
            for key, value in source["build_args"].items():
                if not re.fullmatch(r"[A-Z_]+", key):
                    raise ValueError("Invalid runtime build argument") from None
                arguments.extend(["--build-arg", key + "=" + value])
            docker(
                "build",
                "--file",
                f"infra/local-lab/{stem}.Dockerfile",
                "--tag",
                locked["source"],
                *arguments,
                ".",
                timeout=1800,
            )
            info = json.loads(docker("image", "inspect", locked["source"]))[0]
        else:
            docker("pull", locked["reference"])
            info = json.loads(docker("image", "inspect", locked["reference"]))[0]
    if info["Id"] != locked["config_digest"]:
        raise ValueError("Rebuilt/pulled image differs from reviewed lock: " + name)


def init():
    cfg = state()
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "alerts").mkdir(exist_ok=True)
    print("Preparing digest-pinned local registry images.", flush=True)
    images = image_lock()
    for name in (
        "skopeo",
        "blackbox",
        "distribution",
        "db",
        "redis",
        "proxy",
        "prometheus",
        "alertmanager",
        "node",
        "grafana",
        "loki",
        "alloy",
        "tempo",
        "postgres_exporter",
        "redis_exporter",
        *(["ollama"] if "ollama" in images else []),
    ):
        locked = images[name]
        ensure_image(name, locked)
    packages = sorted(
        (ROOT / ".local/evidence/releases").glob("api-release-*/provenance.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not packages:
        raise RuntimeError("Run make release-package before initializing the lab")
    from .registry import bundle_module

    bundle_module().verify(packages[-1].parent)
    provenance = json.loads(packages[-1].read_text())
    tools = "ai-platform-blueprint-lab-tools:" + cfg["id"]
    docker("tag", provenance["image_config_digest"], tools)
    tls()
    env = {
        "LAB_NEXUS_IMAGE": images["nexus"]["reference"],
        "LAB_CONTROLLER_IMAGE": images["proxy"]["reference"],
        "LAB_TOOLS_IMAGE": tools,
        "LAB_NEXUS_PORT": cfg["nexus_port"],
        "LAB_REGISTRY_PORT": cfg["registry_port"],
        "LAB_UID": os.getuid(),
        "LAB_GID": os.getgid(),
        "LAB_ROOT": ROOT,
        "LAB_STATE": STATE,
        "LAB_SECRETS": PRIVATE,
    }
    (STATE / "compose.env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))
    from .fallback import init as registry_init

    registry_init()
    from .improvements import prober_up

    prober_up()
    cosign()
    print("Local OCI registry and signing material are ready.", flush=True)
