#!/usr/bin/env python3
"""Small, memory-bounded local demo backup; restore cannot target an existing DB."""

import argparse
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from common import COMPOSE, DOCKER, KNOWLEDGE_SQL, ROOT, SNAPSHOT_SQL, run, wait_until
from cryptography.fernet import Fernet, InvalidToken

MAX_DUMP = 64 * 1024 * 1024
IMAGE = "pgvector/pgvector:0.8.1-pg16-bookworm"


def cipher():
    return Fernet((ROOT / ".secrets/backup.key").read_bytes().strip())


def backup():
    directory = ROOT / ".local/backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Stream stdout to bounded memory; do not persist unencrypted data.
    with subprocess.Popen(  # noqa: S603
        COMPOSE
        + [
            "exec",
            "-T",
            "db",
            "pg_dump",
            "-U",
            "platform_admin",
            "-d",
            "platform",
            "--format=custom",
            "--no-owner",
            "--no-acl",
        ],
        stdout=subprocess.PIPE,
    ) as process:
        dump = process.stdout.read(MAX_DUMP + 1)
        if len(dump) > MAX_DUMP:
            process.kill()
            raise ValueError("Demo dump exceeds 64 MiB limit")
        if process.wait() != 0 or not dump.startswith(b"PGDMP"):
            raise ValueError("pg_dump failed")
    encrypted = cipher().encrypt(dump)
    filename = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".fernet"
    path = directory / filename
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encrypted)
    print(f"Encrypted backup: {path.relative_to(ROOT)}")
    return path


def restore(path: Path, expected: Path | None = None, expected_kb: Path | None = None):
    if path.stat().st_size > MAX_DUMP * 2:
        raise ValueError("Archive too large for demo restore")
    # Authenticate before starting any container. No plaintext on disk.
    dump = cipher().decrypt(path.read_bytes())
    if len(dump) > MAX_DUMP or not dump.startswith(b"PGDMP"):
        raise ValueError("Invalid or oversized dump")
    name = "ai-platform-restore-" + uuid.uuid4().hex[:12]
    try:
        run(
            [
                DOCKER,
                "run",
                "--detach",
                "--name",
                name,
                "--label",
                "ai-platform-blueprint.purpose=restore-test",
                "--network",
                "none",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=256m",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                IMAGE,
            ],
            capture_output=True,
        )

        def is_ready():
            result = subprocess.run(  # noqa: S603
                [DOCKER, "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0

        wait_until(is_ready)
        run(
            [
                DOCKER,
                "exec",
                "-i",
                name,
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
            ],
            input=dump,
            capture_output=True,
        )
        result = run(
            [
                DOCKER,
                "exec",
                name,
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                SNAPSHOT_SQL,
            ],
            capture_output=True,
        )
        documents = json.loads(result.stdout)
        if expected is not None and documents != json.loads(expected.read_text()):
            raise ValueError("Restored documents or vectors differ from expected snapshot")
        # Exercise the extension's distance operator, not just table existence.
        run(
            [
                DOCKER,
                "exec",
                name,
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT id FROM documents ORDER BY embedding <=> "
                "(SELECT embedding FROM documents ORDER BY id LIMIT 1) LIMIT 1",
            ],
            capture_output=True,
        )
        if expected_kb is not None:
            result = run(
                [
                    DOCKER,
                    "exec",
                    name,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-At",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    KNOWLEDGE_SQL,
                ],
                capture_output=True,
            )
            restored_kb = json.loads(result.stdout)
            if restored_kb != json.loads(expected_kb.read_text()):
                raise ValueError("Restored knowledge data differs from expected snapshot")
            print(
                "Knowledge restore verified: collections, raw payload hashes, chunks and vectors."
            )
        print(f"Restore verified: {len(documents)} documents; isolated temporary database.")
    finally:
        # Only this invocation's UUID-named container; never broad cleanup.
        subprocess.run(  # noqa: S603
            [DOCKER, "rm", "--force", "--volumes", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore-test"])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--expected-kb", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        backup()
    else:
        archives = sorted((ROOT / ".local/backups").glob("*.fernet"))
        path = args.archive or (archives[-1] if archives else None)
        if path is None:
            parser.error("No backup found; run make backup first")
        try:
            restore(path, args.expected, args.expected_kb)
        except InvalidToken:
            raise SystemExit("Archive authentication failed; no restore started") from None


if __name__ == "__main__":
    main()
