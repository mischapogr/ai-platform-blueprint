#!/usr/bin/env python3
"""Opt-in CPU model qualification with a private runtime/cache and disposable DB."""

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "tests/evaluation"), str(ROOT / "apps/worker")]
from retrieval_benchmark import gate  # noqa: E402
from retrieval_benchmark import run as retrieval_run  # noqa: E402

DOCKER = shutil.which("docker") or "/usr/bin/docker"


def docker(*args, input=None):
    return subprocess.run(  # noqa: S603 - explicit argv, no shell
        [DOCKER, *args],
        input=input,
        text=True,
        check=True,  # noqa: S603
        capture_output=True,
        timeout=60,
        cwd=ROOT,
    ).stdout.strip()


def wait(check):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (httpx.HTTPError, subprocess.CalledProcessError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Private qualification service did not become ready")


def resident_kib(pid):
    """Sample only the owned runtime process tree; no command lines or environment."""
    pending, seen, total = [pid], set(), 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            status = Path(f"/proc/{current}/status").read_text()
            total += next(
                int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:")
            )
            for child_file in Path(f"/proc/{current}/task").glob("*/children"):
                pending.extend(int(child) for child in child_file.read_text().split())
        except (OSError, ValueError, StopIteration):
            continue
    return total or None


def pull(client, model):
    print("Preparing model in project-local cache: " + model, flush=True)
    with client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line and "error" in json.loads(line):
                raise RuntimeError("Model preparation failed; inspect private runtime log")


def api_flow(temp):
    import main as worker
    from fastapi.testclient import TestClient
    from platform_api import main as api_main

    token = Path(temp) / "workspace-token"
    token.write_text("synthetic-workspace-token")
    os.environ["WORKSPACE_TOKEN_FILE"] = str(token)
    os.environ["REQUEST_LOG"] = str(Path(temp) / "requests.jsonl")
    with TestClient(api_main.app) as api:
        api.post("/api/session", json={"token": token.read_text()}).raise_for_status()
        result = api.post(
            "/api/collections",
            json={
                "name": "Local-only model qualification",
                "embedding_provider": "ollama",
                "allow_external": False,
            },
        )
        result.raise_for_status()
        collection = result.json()
        result = api.post(
            f"/api/collections/{collection['id']}/sources",
            json={
                "kind": "text",
                "title": "Synthetic reference",
                "text": "TraceQuartz reference code is VIOLET-731.",
            },
        )
        result.raise_for_status()
        worker.execute(worker.claim())
        query = {
            "question": "What is the TraceQuartz reference code?",
            "collection_id": collection["id"],
            "provider": "ollama",
            "model": "llama3.2:3b",
        }
        result = api.post("/api/chat", json=query)
        result.raise_for_status()
        answer = result.json()
        assert answer["mode"] == "ollama" and answer["sources"] and answer["answer"]
        assert (
            api.post(
                "/api/chat", json={**query, "provider": "openai", "external_consent": True}
            ).status_code
            == 403
        )
        api.delete("/api/collections/" + collection["id"]).raise_for_status()
        return {
            "local_only_generation_and_worker_ingestion": True,
            "external_provider_blocked": True,
            "answer": answer["answer"],
            "usage": answer["usage"],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("semantic", "answers", "all"), default="all")
    parser.add_argument(
        "--endpoint", help="Existing loopback-only runtime; never pulled, stopped or modified"
    )
    parser.add_argument("--database-image", help="Reviewed local database image config digest")
    args = parser.parse_args()
    if args.database_image:
        import re

        if not re.fullmatch(r"sha256:[a-f0-9]{64}", args.database_image):
            raise ValueError("Database override must be an image config digest")
    binary = shutil.which("ollama")
    if not binary and not args.endpoint:
        raise RuntimeError("Install Ollama locally before running opt-in CPU qualification")
    evidence = ROOT / ".local/evidence"
    cache = ROOT / ".local/models/qualification"
    evidence.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    run_id = "ai-blueprint-models-" + uuid.uuid4().hex[:12]
    db_name = run_id + "-db"
    owned_db = False
    server = None
    stop_monitor = threading.Event()
    memory_samples = []
    monitor = None
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = args.endpoint or f"http://127.0.0.1:{port}"
    if args.endpoint:
        import ipaddress
        from urllib.parse import urlsplit

        parsed = urlsplit(base)
        if (
            parsed.scheme != "http"
            or not ipaddress.ip_address(parsed.hostname).is_loopback
            or parsed.port is None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Existing runtime must be an explicit loopback HTTP endpoint")
    env = {
        **os.environ,
        "OLLAMA_HOST": base,
        "OLLAMA_MODELS": str(cache),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "CUDA_VISIBLE_DEVICES": "-1",
    }
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("disposable", ROOT / "scripts/evaluate-retrieval.py")
    disposable = module_from_spec(spec)
    spec.loader.exec_module(disposable)
    tested_inputs = disposable.input_hash()
    report = {
        "run_id": run_id,
        "time_utc": datetime.now(UTC).isoformat(),
        "input_sha256": tested_inputs,
        "host": {
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "inference_threads": 4,
            "gpu_layers": 0,
        },
    }
    revision = subprocess.run(  # noqa: S603
        [shutil.which("git") or "/usr/bin/git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    report["revision"] = revision
    memory_total = next(
        (
            int(line.split()[1]) / 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        ),
        None,
    )
    report["host"]["memory_total_mib"] = memory_total
    try:
        if not args.endpoint:
            with (evidence / (run_id + "-runtime.log")).open("w") as log:
                server = subprocess.Popen(  # noqa: S603 - resolved local Ollama binary
                    [binary, "serve"],
                    env=env,
                    stdout=log,  # noqa: S603
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=ROOT,
                )

            def sample_memory():
                while not stop_monitor.is_set():
                    observed = resident_kib(server.pid)
                    if observed is not None:
                        memory_samples.append(observed)
                    stop_monitor.wait(0.25)

            monitor = threading.Thread(target=sample_memory, daemon=True)
            monitor.start()
        with httpx.Client(base_url=base, trust_env=False, timeout=120) as client:
            wait(lambda: client.get("/api/version").status_code == 200)
            report["runtime"] = client.get("/api/version").json()
            report["existing_loopback_runtime"] = bool(args.endpoint)
            if not args.endpoint:
                for model in ("nomic-embed-text:latest", "llama3.2:3b"):
                    pull(client, model)
        docker(
            "create",
            "--name",
            db_name,
            "--label",
            "ai-blueprint.role=evaluation",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=256m",
            "-e",
            "POSTGRES_DB=platform",
            "-e",
            "POSTGRES_USER=platform_admin",
            "-e",
            "POSTGRES_PASSWORD=synthetic-benchmark-only",
            args.database_image or "pgvector/pgvector:0.8.1-pg16-bookworm",
        )
        owned_db = True
        docker("start", db_name)
        wait(
            lambda: docker(
                "exec",
                db_name,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "platform_admin",
                "-d",
                "platform",
            )
        )
        db_port = docker("port", db_name, "5432/tcp").split(":")[-1]
        sql = (
            "CREATE EXTENSION vector; CREATE ROLE platform_app LOGIN "
            "PASSWORD 'synthetic-benchmark-only';\n"
            + "\n".join(
                p.read_text() for p in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql"))
            )
        )
        docker(
            "exec",
            "-i",
            db_name,
            "psql",
            "-U",
            "platform_admin",
            "-d",
            "platform",
            "-v",
            "ON_ERROR_STOP=1",
            input=sql,
        )
        with tempfile.TemporaryDirectory(
            prefix="model-qualification-", dir=ROOT / ".local"
        ) as temp:
            password = Path(temp) / "password"
            password.write_text("synthetic-benchmark-only")
            password.chmod(0o600)
            os.environ.update(
                DB_HOST="127.0.0.1",
                DB_PORT=db_port,
                DB_PASSWORD_FILE=str(password),
                REDIS_HOST="127.0.0.1",
                OPENAI_API_KEY_FILE=str(Path(temp) / "absent-key"),
                OLLAMA_BASE_URL=base,
                OLLAMA_CHAT_MODELS="llama3.2:3b",
                OLLAMA_EMBEDDING_MODEL="nomic-embed-text:latest",
                OLLAMA_EMBEDDING_PROFILE="nomic-v1",
                PYTHONPATH=str(ROOT / "apps/api"),
            )
            os.environ.pop("PLATFORM_TRACE_ENDPOINT", None)
            from platform_api import db, local_models

            report["embedding_model"] = local_models.embedding_config()
            report["generation_model"] = local_models.identity("llama3.2:3b", "completion")
            print(
                "Checking authenticated local-only indexing and generation through the API.",
                flush=True,
            )
            report["api_flow"] = api_flow(temp)
            if args.suite in {"semantic", "all"}:
                print("Running the fixed 48-question hash baseline.", flush=True)
                offline = retrieval_run()
                failures = gate(
                    offline, json.loads((ROOT / "tests/evaluation/baseline.json").read_text())
                )
                if failures:
                    raise RuntimeError("Offline baseline failed: " + "; ".join(failures))
                print(
                    "Running real local semantic ingestion and 48 retrieval questions.", flush=True
                )
                semantic = retrieval_run("ollama")
                if semantic["leakage_count"]:
                    raise RuntimeError("Local semantic retrieval leaked a collection")
                report.update(offline=offline, semantic=semantic)
            if args.suite in {"answers", "all"}:
                from answer_evaluation import run as answer_run

                print(
                    "Evaluating actual local answers with fixed and retrieved evidence.", flush=True
                )
                report["answers"] = answer_run()
            with db.connect() as conn:
                report["usage"] = conn.execute(
                    "SELECT model,operation,count(*) AS calls,sum(input_tokens) AS input_tokens,"
                    "sum(output_tokens) AS output_tokens FROM kb_usage GROUP BY model,operation"
                ).fetchall()
        if disposable.input_hash() != tested_inputs:
            raise RuntimeError("Qualification inputs changed during execution; rerun without edits")
        stop_monitor.set()
        if monitor is not None:
            monitor.join(timeout=2)
        report["host"]["sampled_peak_sum_rss_mib"] = (
            max(memory_samples) / 1024 if memory_samples else None
        )
        report["host"]["memory_sample_count"] = len(memory_samples)
        target = evidence / (run_id + ".json")
        target.write_text(json.dumps(report, indent=2, default=str) + "\n")
        for key in ("offline", "semantic", "answers"):
            if key in report:
                print(key, json.dumps(report[key]["summary"]))
        print("Report: " + str(target.relative_to(ROOT)))
    finally:
        stop_monitor.set()
        if monitor is not None:
            monitor.join(timeout=2)
        try:
            if owned_db:
                docker("rm", "-f", "-v", db_name)
        finally:
            if server is not None and server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
