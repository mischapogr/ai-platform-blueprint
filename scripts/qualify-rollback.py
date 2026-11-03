#!/usr/bin/env python3
"""Synthetic image upgrade/rollback qualification; never connects to a workspace DB."""

import argparse
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_artifacts", ROOT / "scripts/release-artifacts.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
GIT = shutil.which("git") or "/usr/bin/git"

PROGRAM = (ROOT / "scripts/fixtures/rollback_application.py").read_text()


def git(*args, binary=False):
    result = subprocess.run([GIT, *args], cwd=ROOT, check=True, capture_output=True)  # noqa: S603
    return result.stdout if binary else result.stdout.decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        default="574e97a3bf816ab46bfdf0149315e185d8cfdae3",
        help="Local qualified baseline revision used as rollback target",
    )
    args = parser.parse_args()
    revision = git("rev-parse", "--verify", args.reference + "^{commit}")
    before = release.materials()
    name = "ai-blueprint-rollback-" + uuid.uuid4().hex[:12]
    db_name = name + "-db"
    reference_tag, candidate_tag = (
        "ai-platform-blueprint-reference:" + name,
        "ai-platform-blueprint-candidate:" + name,
    )
    network_created = db_created = False
    try:
        with tempfile.TemporaryDirectory(prefix="ai-blueprint-reference-", dir="/tmp") as temp:
            archive = git(
                "archive",
                revision,
                ".dockerignore",
                "LICENSE",
                "NOTICE",
                "requirements.txt",
                "apps/api",
                "apps/worker",
                "apps/demo-rag/frontend",
                "docs/runbooks",
                binary=True,
            )
            with tarfile.open(fileobj=io.BytesIO(archive)) as source:
                source.extractall(temp, filter="data")
            release.run(
                "build",
                "--tag",
                reference_tag,
                "--file",
                str(Path(temp) / "apps/api/Dockerfile"),
                temp,
            )
        release.run("build", "--tag", candidate_tag, "--file", "apps/api/Dockerfile", ".")
        reference = json.loads(release.run("image", "inspect", reference_tag))[0]["Id"]
        candidate = json.loads(release.run("image", "inspect", candidate_tag))[0]["Id"]
        release.run(
            "network", "create", "--internal", "--label", "ai-blueprint.role=evaluation", name
        )
        network_created = True
        release.run(
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
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=256m",
            "-e",
            "POSTGRES_DB=platform",
            "-e",
            "POSTGRES_USER=platform_admin",
            "-e",
            "POSTGRES_PASSWORD=synthetic-benchmark-only",
            "pgvector/pgvector:0.8.1-pg16-bookworm",
        )
        db_created = True
        release.run("start", db_name)
        deadline = time.monotonic() + 60
        while True:
            try:
                release.run(
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
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "Disposable rollback database did not become ready"
                    ) from None
                time.sleep(0.5)

        def sql(text):
            return subprocess.run(  # noqa: S603 - explicit argv, no shell
                [
                    release.DOCKER,
                    "exec",
                    "-i",
                    db_name,
                    "psql",
                    "-U",
                    "platform_admin",
                    "-d",
                    "platform",
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],  # noqa: S603
                input=text,
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout.strip()

        sql(
            "CREATE EXTENSION vector; CREATE ROLE platform_app LOGIN "
            "PASSWORD 'synthetic-benchmark-only';\n"
            + git("show", revision + ":infra/compose/postgres/002-knowledge.sql")
        )

        def app(image, action):
            return json.loads(
                release.run(
                    "run",
                    "--rm",
                    "--network",
                    name,
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--memory",
                    "256m",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/tmp:size=16m,mode=1777",  # noqa: S108
                    "-e",
                    "DB_HOST=db",
                    "-e",
                    "DB_PASSWORD_FILE=/tmp/rollback-password",
                    image,
                    "python",
                    "-c",
                    PROGRAM,
                    json.dumps(action),
                    timeout=60,
                )
            )

        initial = app(
            reference, {"kind": "write", "text": "RollbackQuartz checkpoint code is VIOLET-731."}
        )
        collection_id = initial["collection_id"]
        snapshot_query = (
            "SELECT COALESCE(json_agg(c ORDER BY id),'[]'::json) FROM "
            "(SELECT id,source_id,content,ordinal,embedding::text FROM kb_chunks) c;"
        )
        baseline = sql(snapshot_query)
        for migration in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql")):
            sql(migration.read_text())
        assert sql(snapshot_query) == baseline, "Migration changed the existing index"
        query = {
            "kind": "query",
            "question": "RollbackQuartz checkpoint code",
            "collection_id": collection_id,
        }
        assert "VIOLET-731" in app(candidate, query)["answer"]
        app(
            candidate,
            {
                "kind": "write",
                "collection_id": collection_id,
                "text": "RollbackOrchid checkpoint code is AZURE-812.",
            },
        )
        candidate_snapshot = sql(snapshot_query)
        supported = release.capabilities(reference)
        release.ensure_compatible(app(candidate, {"kind": "providers"}), supported)
        assert "VIOLET-731" in app(reference, query)["answer"]
        assert (
            "AZURE-812"
            in app(reference, {**query, "question": "RollbackOrchid checkpoint code"})["answer"]
        )
        assert sql(snapshot_query) == candidate_snapshot
        app(candidate, {"kind": "local-metadata"})
        try:
            release.ensure_compatible(app(candidate, {"kind": "providers"}), supported)
        except ValueError:
            blocked = True
        else:
            raise AssertionError("Unsupported-provider rollback was allowed")
        assert release.materials() == before, "Candidate inputs changed during qualification"
        report = {
            "time_utc": datetime.now(UTC).isoformat(),
            "reference_revision": revision,
            "reference_image": reference,
            "candidate_image": candidate,
            "source_materials_sha256": before,
            "checks": {
                "populated_index_preserved_on_upgrade": True,
                "candidate_write_read_after_rollback": True,
                "unsupported_local_provider_rollback_blocked": blocked,
            },
            "limits": (
                "local direct application calls; no HTTP/session/TLS, signing "
                "or remote rollout qualification"
            ),
        }
        target = ROOT / ".local/evidence" / (name + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["checks"], indent=2))
        print("Report: " + str(target.relative_to(ROOT)))
    finally:
        try:
            if db_created:
                release.run("rm", "-f", "-v", db_name)
        finally:
            if network_created:
                release.run("network", "rm", name)


if __name__ == "__main__":
    main()
