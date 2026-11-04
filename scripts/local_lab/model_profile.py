"""Optional CPU-only Ollama service on the guest's private application network."""

import json
import os
import subprocess
from contextlib import contextmanager

import httpx

from . import acceptance, deploy, vm
from .common import ROOT, STATE, free_port, image_lock, save, state, wait


def configure(config, images, cfg, target=None):
    if not cfg.get("models_enabled"):
        return
    if "ollama" not in images:
        raise ValueError("Publish the reviewed local model runtime first")
    config["services"]["ollama"] = {
        "image": images["ollama"],
        "restart": "unless-stopped",
        "user": "65534:65534",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "networks": ["backend"],
        "mem_limit": "4608m",
        "cpus": 2,
        "pids_limit": 256,
        "volumes": ["./model-cache:/models:ro"],
        "tmpfs": ["/tmp:size=64m,mode=1777"],  # noqa: S108 - bounded container tmpfs
        "environment": {
            "OLLAMA_NO_CLOUD": "1",
            # Seeded models are immutable; startup pruning would remove empty manifests-v2.
            "OLLAMA_NOPRUNE": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_MAX_QUEUE": "4",
            "OLLAMA_CONTEXT_LENGTH": "8192",
            "OLLAMA_KEEP_ALIVE": "0",
            "CUDA_VISIBLE_DEVICES": "-1",
        },
        "healthcheck": {
            "test": ["CMD", "/usr/bin/ollama", "list"],
            "interval": "10s",
            "timeout": "4s",
            "retries": 4,
            "start_period": "60s",
        },
        "logging": {"driver": "json-file", "options": {"max-size": "2m", "max-file": "2"}},
    }
    for service in ("api", "worker"):
        config["services"][service]["environment"].update(
            {
                "OLLAMA_BASE_URL": "http://ollama:11434",
                "OLLAMA_CHAT_MODELS": "llama3.2:3b",
                "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text:latest",
                "OLLAMA_EMBEDDING_PROFILE": "nomic-v1",
            }
        )

    if target is not None:
        path = target / "monitoring/alerts.yaml"
        rules = json.loads(path.read_text())
        rules["groups"][0]["rules"].append(
            {
                "alert": "ModelRuntimeUnavailable",
                "expr": 'blueprint_container_health{service="ollama"} == 0 '
                'or absent(blueprint_container_health{service="ollama"})',
                "for": "30s",
                "labels": {"severity": "warning", "job": "ollama"},
                "annotations": {
                    "summary": "Local model runtime is unhealthy; source search remains available",
                    "runbook_url": f"https://localhost:{cfg['https_port']}/grafana/d/platform-overview",
                },
            }
        )
        save(path, rules, 0o644)


def transfer_models(name):
    source = ROOT / ".local/models/qualification"
    for directory in ("blobs", "manifests"):
        path = source / directory
        if not path.is_dir() or path.is_symlink():
            raise ValueError("Expected project-owned qualified model cache missing")
        if any(p.is_symlink() for p in path.rglob("*")):
            raise ValueError("Model cache contains an unreviewed symlink")
    destination = deploy.GUEST + "/model-cache"
    vm.ssh(name, "sudo mkdir -p " + destination)
    args = vm.ssh_args(name) + ["sudo tar --no-same-owner -xf - -C " + destination]
    archive = subprocess.Popen(  # noqa: S603 - only the two owned public model cache directories
        ["/usr/bin/tar", "-C", str(source), "-cf", "-", "blobs", "manifests"],
        stdout=subprocess.PIPE,
    )
    try:
        result = subprocess.run(args, stdin=archive.stdout, capture_output=True, timeout=600)  # noqa: S603
        archive.stdout.close()
        if archive.wait(timeout=10) or result.returncode:
            raise RuntimeError("Private guest model-cache transfer failed")
    finally:
        if archive.poll() is None:
            archive.terminate()
            archive.wait(timeout=10)
    vm.ssh(name, "sudo mkdir -p " + destination + "/manifests-v2")
    vm.ssh(name, "sudo chmod -R a+rX " + destination)


def enable(name):
    cfg = state()
    if cfg.get("models_enabled"):
        raise ValueError("Model profile already enabled; run model checks")
    transfer_models(name)
    from .automation import execute

    execute("backup", name)
    # Retain disk and all volumes. Only the owned guest is rebooted for its RAM/CPU budget.
    acceptance.command(name, "stop", timeout=180)
    vm.ssh(name, "sudo systemctl poweroff", check=False, timeout=15)

    def stopped():
        from pathlib import Path

        pidfile = vm.directory(name) / "qemu.pid"
        if not pidfile.exists():
            return True
        process = Path(f"/proc/{int(pidfile.read_text())}/cmdline")
        return not process.exists() or not process.read_bytes()

    wait(stopped, seconds=90)
    tunnel = vm.directory(name) / "registry-tunnel.pid"
    if tunnel.exists():
        pid = int(tunnel.read_text())
        from pathlib import Path

        process = Path(f"/proc/{pid}/cmdline")
        try:
            command = process.read_bytes()
        except FileNotFoundError:
            command = b""
        if deploy.owns_tunnel(command, vm.directory(name)):
            os.kill(pid, 15)
        # A stale/reused PID is never signalled; only our stale ledger is retired.
        tunnel.unlink()
    cfg.update(vm_memory_mib=8192, vm_cpus=4, models_enabled=True)
    save(STATE / "state.json", cfg)
    marker = vm.create(name)
    if marker.get("memory_mib", 0) < 8192:
        raise RuntimeError("Owned guest did not boot with the required model memory budget")
    deploy.apply(name)
    print("Local CPU model profile enabled on the retained guest disk (8 GiB / 4 vCPU).")


@contextmanager
def endpoint(name):
    import ipaddress
    import re

    container = acceptance.command(name, "ps -q ollama").stdout.decode().strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise ValueError("Unknown owned model container")
    address = (
        vm.ssh(
            name,
            "sudo docker inspect --format "
            "'{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' " + container,
        )
        .stdout.decode()
        .strip()
    )
    if not ipaddress.ip_address(address).is_private:
        raise ValueError("Model runtime is outside the owned private guest network")
    port = free_port(0)
    args = vm.ssh_args(name)
    args = args[:-1] + [
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        f"127.0.0.1:{port}:{address}:11434",
        args[-1],
    ]
    with (STATE / "model-evaluation-tunnel.log").open("ab") as log:
        tunnel = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(trust_env=False, timeout=3) as client:
            wait(lambda: client.get(base + "/api/version").status_code == 200, seconds=20)
        yield base
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait(timeout=5)


def evaluate(name):
    with endpoint(name) as base:
        subprocess.run(  # noqa: S603 - reviewed local driver, explicit own loopback tunnel
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/qualify-models.py"),
                "--suite",
                "answers",
                "--endpoint",
                base,
                "--database-image",
                image_lock()["db"]["config_digest"],
            ],
            check=True,
            cwd=ROOT,
            timeout=3600,
        )
