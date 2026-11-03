#!/usr/bin/env python3
"""Run the synthetic benchmark in isolated containers, never the live workspace."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "tests/evaluation"
sys.path.insert(0, str(EVALUATION))
from retrieval_benchmark import gate  # noqa: E402

IMAGE = "ai-platform-blueprint-retrieval-eval:local"
DB_IMAGE = "pgvector/pgvector:0.8.1-pg16-bookworm"
DOCKER = shutil.which("docker") or "/usr/bin/docker"
GIT = shutil.which("git") or "/usr/bin/git"


def docker(*args, input=None, timeout=120, include_stderr=False):
    result = subprocess.run(  # noqa: S603
        [DOCKER, *args],
        input=input,
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
        cwd=ROOT,
    )
    return (result.stdout + (result.stderr if include_stderr else "")).strip()


def input_hash():
    paths = [
        ROOT / "requirements.txt",
        ROOT / "uv.lock",
        Path(__file__).resolve(),
        EVALUATION / "corpus.json",
        EVALUATION / "answer_cases.json",
        ROOT / "scripts/qualify-models.py",
    ]
    for directory in (ROOT / "apps/api/platform_api", ROOT / "apps/worker", EVALUATION):
        paths.extend(directory.glob("*.py"))
    paths.extend((ROOT / "infra/compose/postgres").glob("[0-9]*.sql"))
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("retrieval", "queue", "tracing"), default="retrieval")
    parser.add_argument(
        "--record-baseline",
        action="store_true",
        help="Explicitly replace the reviewed synthetic baseline after measurement",
    )
    args = parser.parse_args()
    if args.record_baseline and args.suite != "retrieval":
        parser.error("Only retrieval has a replaceable quality baseline")
    baseline_path = EVALUATION / "baseline.json"
    name = "ai-blueprint-eval-" + uuid.uuid4().hex[:12]
    db_name, runner_name = name + "-db", name + "-runner"
    owned = []
    network_created = False
    cleanup_errors = []
    tested_inputs = input_hash()
    try:
        print("Building dedicated benchmark image (cached layers may be reused).", flush=True)
        docker("build", "-t", IMAGE, "-f", "apps/api/Dockerfile", ".", timeout=600)
        image_id = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
        docker("network", "create", "--internal", "--label", "ai-blueprint.role=evaluation", name)
        network_created = True
        # Names are fresh, and every deletion below is limited to resources from this run.
        docker(
            "create",
            "--name",
            db_name,
            "--network",
            name,
            "--network-alias",
            "db",
            "--label",
            "ai-blueprint.role=evaluation",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=256m",
            "-e",
            "POSTGRES_DB=platform",
            "-e",
            "POSTGRES_USER=platform_admin",
            "-e",
            "POSTGRES_PASSWORD=synthetic-benchmark-only",
            DB_IMAGE,
        )
        owned.append(db_name)
        docker("start", db_name)
        deadline = time.monotonic() + 60
        while True:
            try:
                docker(
                    "exec",
                    db_name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "platform_admin",
                    "-d",
                    "platform",
                    timeout=5,
                )
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable database did not become ready") from None
                time.sleep(1)
        sql = (
            "CREATE EXTENSION vector; "
            "CREATE ROLE platform_app LOGIN PASSWORD 'synthetic-benchmark-only';\n"
            + "\n".join(
                path.read_text()
                for path in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql"))
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
        if args.suite == "tracing":
            tempo_name = name + "-tempo"
            docker(
                "create",
                "--name",
                tempo_name,
                "--network",
                name,
                "--network-alias",
                "tempo",
                "--label",
                "ai-blueprint.role=evaluation",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "384m",
                "--cpus",
                "0.5",
                "--user",
                "10001:10001",
                "--tmpfs",
                "/tmp:size=128m,mode=1777",  # noqa: S108
                "--mount",
                f"type=bind,src={ROOT / 'observability/tempo/tempo.yml'},"
                "dst=/etc/tempo.yml,readonly",
                "grafana/tempo:2.8.2",
                "-config.file=/etc/tempo.yml",
            )
            owned.append(tempo_name)
            docker("start", tempo_name)
        script = {
            "retrieval": "retrieval_benchmark.py",
            "queue": "queue_qualification.py",
            "tracing": "tracing_qualification.py",
        }[args.suite]
        docker(
            "create",
            "--name",
            runner_name,
            "--network",
            name,
            "--label",
            "ai-blueprint.role=evaluation",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,size=16m,mode=1777",  # noqa: S108 - private container tmpfs
            "--mount",
            f"type=bind,src={EVALUATION},dst=/benchmark,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'apps/api/platform_api'},dst=/app/platform_api,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'apps/worker'},dst=/app/worker,readonly",
            "-e",
            "PYTHONPATH=/app",
            "-e",
            "DB_HOST=db",
            "-e",
            "DB_PASSWORD_FILE=/tmp/benchmark-password",
            "-e",
            "REDIS_HOST=127.0.0.1",
            image_id,
            "python",
            f"/benchmark/{script}",
        )
        owned.append(runner_name)
        docker("start", runner_name)
        try:
            status = docker("wait", runner_name, timeout=120)
        except subprocess.TimeoutExpired:
            diagnostic = ROOT / ".local/evidence" / (name + "-timeout.log")
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            diagnostic.write_text(docker("logs", "--tail", "80", runner_name, include_stderr=True))
            raise RuntimeError(f"Qualification exceeded 120s; diagnostics: {diagnostic}") from None
        if status != "0":
            raise RuntimeError(
                "Benchmark runner failed: " + docker("logs", runner_name, include_stderr=True)
            )
        report = json.loads(docker("logs", runner_name))
        if input_hash() != tested_inputs:
            raise RuntimeError("Qualification inputs changed during execution; rerun without edits")
        revision = subprocess.run(  # noqa: S603
            [GIT, "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip()
        source_hash = hashlib.sha256()
        for path in sorted((ROOT / "apps/api/platform_api").glob("*.py")):
            source_hash.update(path.name.encode() + b"\0" + path.read_bytes())
        report["execution"] = {
            "time_utc": datetime.now(UTC).isoformat(),
            "revision": revision,
            "application_sha256": source_hash.hexdigest(),
            "benchmark_sha256": hashlib.sha256((EVALUATION / script).read_bytes()).hexdigest(),
            "input_sha256": tested_inputs,
            "image_id": image_id,
            "database_image_id": docker("image", "inspect", DB_IMAGE, "--format", "{{.Id}}"),
            "scope": {
                "retrieval": "local disposable PostgreSQL; direct ingestion/retrieval",
                "queue": "local disposable PostgreSQL; real child process crash tests",
                "tracing": "local disposable PostgreSQL/Tempo; HTTP, worker and child",
            }[args.suite],
        }
        if args.suite != "retrieval":
            failures = report.get("failures", [])
        elif args.record_baseline:
            failures = ["Collection leakage detected"] if report["leakage_count"] else []
            if not failures:
                baseline = {
                    key: report[key]
                    for key in (
                        "corpus_sha256",
                        "corpus_version",
                        "protocol",
                        "configuration",
                        "summary",
                        "execution",
                    )
                }
                baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")
        else:
            failures = gate(report, json.loads(baseline_path.read_text()))
        report["gate_failures"] = failures
        evidence = ROOT / ".local/evidence" / (name + ".json")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["summary"], indent=2))
        print(f"Report: {evidence.relative_to(ROOT)}")
        if failures:
            raise RuntimeError("; ".join(failures))
        print(
            "Baseline recorded; inspect measured quality before accepting it."
            if args.record_baseline
            else f"{args.suite.capitalize()} qualification passed."
        )
    finally:
        for container in reversed(owned):
            try:
                docker("rm", "-f", "-v", container)
            except (subprocess.SubprocessError, OSError):
                cleanup_errors.append(container)
        if network_created:
            try:
                docker("network", "rm", name)
            except (subprocess.SubprocessError, OSError):
                cleanup_errors.append(name)
        if cleanup_errors:
            raise RuntimeError(
                "Cleanup failed; inspect these exact resources: " + ", ".join(cleanup_errors)
            )


if __name__ == "__main__":
    try:
        main()
    except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
