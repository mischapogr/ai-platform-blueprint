"""Quiesced logical PostgreSQL migration into a new owned volume; retain the old one."""

import fcntl
import hashlib
import json
import re
import shlex
import uuid

from cryptography.fernet import Fernet

from . import acceptance, deploy, recovery, registry, vm
from .common import PRIVATE, ROOT, STATE, save, state, wait


def guest(name, args, **kwargs):
    return vm.ssh(name, shlex.join(args), **kwargs)


def write_guest(name, path, content):
    allowed = {deploy.GUEST + "/compose.yaml"}
    if path not in allowed:
        raise ValueError("Unapproved migration write path")
    code = (
        "import sys; from pathlib import Path; Path("
        + repr(path)
        + ").write_bytes(sys.stdin.buffer.read())"
    )
    guest(name, ["sudo", "python3", "-c", code], input=content)


def apply(name):
    cfg = state()
    if cfg.get("database_volume"):
        raise ValueError("Database already uses an explicit migrated volume; refusing repeat")
    signed = registry.verify()
    reference = signed["images"]["db"]["reference"]
    locked = json.loads((ROOT / "infra/local-lab/images.lock.json").read_text())["db"]
    if locked.get("runtime_uid") != 70:
        raise ValueError("Migration requires the reviewed Alpine PostgreSQL candidate")
    receipt = json.loads((STATE / "alpine-database-restore-receipt.json").read_text())
    if receipt["image"] != locked["config_digest"] or not receipt["full_knowledge_snapshot_equal"]:
        raise ValueError("Isolated candidate restore evidence does not match")
    old = guest(name, ["sudo", "cat", deploy.GUEST + "/compose.yaml"]).stdout
    config = json.loads(old)
    new_volume = "ai-blueprint-" + cfg["id"] + "-database-alpine-" + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"ai-blueprint-[a-f0-9]{10}-database-alpine-[a-f0-9]{8}", new_volume):
        raise ValueError("Invalid owned volume identifier")
    temporary = "blueprint-db-migration-" + cfg["id"]
    (STATE / "automation").mkdir(exist_ok=True, mode=0o700)
    journal = {
        "vm": name,
        "new_volume": new_volume,
        "old_volume_retained": True,
        "image": locked["config_digest"],
        "completed": False,
    }
    with (STATE / "automation/execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Pull before entering maintenance; it does not alter the current database.
        guest(name, ["sudo", "docker", "pull", reference], timeout=600)
        acceptance.command(name, "stop proxy api worker")
        committed = False
        try:
            record, golden = recovery.backup(name)
            with recovery.store_client() as store:
                response = store.get("/backup-store/" + record["filename"])
                response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != record["encrypted_sha256"]:
                raise ValueError("Archive checksum mismatch")
            archive = recovery.decrypt(
                Fernet((PRIVATE / "escrow/backup.key").read_bytes()), response.content
            )
            journal["archive_sha256"] = record["encrypted_sha256"]
            save(STATE / "database-migration.json", journal)
            guest(
                name,
                [
                    "sudo",
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    "ai-blueprint-lab=" + cfg["id"],
                    new_volume,
                ],
            )
            args = [
                "sudo",
                "docker",
                "run",
                "--detach",
                "--name",
                temporary,
                "--label",
                "ai-blueprint-lab=" + cfg["id"],
                "--network",
                "none",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--log-driver",
                "none",
                "-e",
                "POSTGRES_USER=platform_admin",
                "-e",
                "POSTGRES_DB=platform",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/db_password",
                "--mount",
                f"type=volume,src={new_volume},dst=/var/lib/postgresql/data",
            ]
            for secret in ("db_password", "app_password"):
                args += [
                    "--mount",
                    "type=bind,src="
                    + deploy.GUEST
                    + "/secrets/"
                    + secret
                    + ",dst=/run/secrets/"
                    + secret
                    + ",readonly",
                ]
            args += [
                "--mount",
                "type=bind,src="
                + deploy.GUEST
                + "/postgres/init.sh,dst=/docker-entrypoint-initdb.d/10-init.sh,readonly",
                reference,
            ]
            guest(name, args)
            wait(
                lambda: (
                    guest(
                        name,
                        [
                            "sudo",
                            "docker",
                            "exec",
                            temporary,
                            "pg_isready",
                            "-h",
                            "127.0.0.1",
                            "-U",
                            "platform_admin",
                        ],
                        check=False,
                    ).returncode
                    == 0
                ),
                seconds=60,
            )
            guest(
                name,
                [
                    "sudo",
                    "docker",
                    "exec",
                    "-i",
                    temporary,
                    "pg_restore",
                    "-U",
                    "platform_admin",
                    "-d",
                    "platform",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                ],
                input=archive,
            )
            result = guest(
                name,
                [
                    "sudo",
                    "docker",
                    "exec",
                    temporary,
                    "psql",
                    "-U",
                    "platform_admin",
                    "-d",
                    "platform",
                    "-At",
                    "-c",
                    recovery.SNAPSHOT,
                ],
            )
            if recovery.normalize_snapshot(json.loads(result.stdout)) != golden:
                raise AssertionError("Candidate knowledge snapshot differs")
            guest(name, ["sudo", "docker", "stop", temporary])
            guest(name, ["sudo", "docker", "rm", temporary])
            acceptance.command(name, "stop db")
            config["services"]["db"]["image"] = reference
            config["volumes"]["database"] = {"external": True, "name": new_volume}
            write_guest(name, deploy.GUEST + "/compose.yaml", json.dumps(config).encode())
            acceptance.command(name, "up --detach --wait db")
            acceptance.command(
                name,
                "exec -T db psql -U platform_admin -d platform "
                "-v ON_ERROR_STOP=1 -f /monitoring/postgres-metrics-role.sql",
            )
            acceptance.command(
                name,
                "exec -T db psql -U platform_admin -d platform "
                "-v ON_ERROR_STOP=1 -c 'GRANT SELECT, INSERT ON documents TO platform_app'",
            )
            for migration in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql")):
                acceptance.command(
                    name,
                    "exec -T db psql -U platform_admin -d platform "
                    "-v ON_ERROR_STOP=1 -f /migrations/" + migration.name,
                )
            if recovery.snapshot(name) != golden:
                raise AssertionError("Knowledge changed at cutover")
            acceptance.command(name, "up --detach --wait api worker")
            result = acceptance.command(
                name,
                "exec -T api python -c "
                + shlex.quote(
                    "import urllib.request; assert urllib.request.urlopen('http://localhost:8000/health/ready').status==200"
                ),
            )
            cfg["database_volume"] = new_volume
            save(STATE / "state.json", cfg)
            journal.update(
                completed=True,
                knowledge_snapshot_equal=True,
                original_volume_unchanged=True,
                api_and_worker_healthy=True,
            )
            save(STATE / "database-migration.json", journal)
            committed = True
        except Exception as error:
            journal["error_class"] = type(error).__name__
            save(STATE / "database-migration.json", journal)
            if not committed:
                acceptance.command(name, "stop api worker db", check=False)
                write_guest(name, deploy.GUEST + "/compose.yaml", old)
                acceptance.command(name, "up --detach --wait db api worker")
            raise
        finally:
            guest(name, ["sudo", "docker", "rm", "--force", temporary], check=False)
            # Never remove either database volume, including on failure.
            acceptance.command(name, "up --detach proxy")
    print("Logical migration passed; knowledge hashes/vectors match; original volume retained.")
