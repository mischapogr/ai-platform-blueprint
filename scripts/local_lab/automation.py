"""Project-owned workflow, encrypted backup and isolated database restore schedules."""

import fcntl
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from . import acceptance, recovery
from .common import PRIVATE, ROOT, STATE, docker, image_lock, run, save, state, wait

JOBS = {
    "workflow": ("*:0/15", 1800),
    "backup": ("*-*-* 19:20:00", 93600),
    "restore": ("*-*-* 19:40:00", 93600),
}
AUTOMATION = STATE / "automation"


def workflow():
    # Delete only the collection created by this invocation, never a name match.
    with acceptance.client() as api:
        response = api.post("/api/collections", json={"name": "Scheduled synthetic workflow"})
        response.raise_for_status()
        collection = response.json()["id"]
        try:
            response = api.post(
                f"/api/collections/{collection}/sources",
                json={
                    "kind": "text",
                    "title": "Scheduled synthetic reference",
                    "text": "QuartzBeacon recovery drill uses a fourteen day retention window.",
                },
            )
            response.raise_for_status()

            def ready():
                result = api.get(f"/api/collections/{collection}/sources")
                result.raise_for_status()
                rows = result.json()
                if any(row["status"] == "failed" for row in rows):
                    raise AssertionError("Synthetic ingestion failed")
                return rows and all(row["status"] == "ready" for row in rows)

            wait(ready, seconds=90)
            acceptance.query(api, collection)
        finally:
            api.delete(f"/api/collections/{collection}").raise_for_status()


def restore(*, image=None, uid=999):
    index = json.loads((STATE / "backup-index.json").read_text())
    record = index[-1]
    cipher = Fernet((PRIVATE / "escrow/backup.key").read_bytes())
    with recovery.store_client() as store:
        response = store.get("/backup-store/" + record["filename"])
        response.raise_for_status()
    encrypted = response.content
    if hashlib.sha256(encrypted).hexdigest() != record["encrypted_sha256"]:
        raise ValueError("Encrypted archive checksum changed")
    archive = recovery.decrypt(cipher, encrypted)
    golden = cipher.decrypt((STATE / "golden" / record["filename"]).read_bytes())
    if hashlib.sha256(golden).hexdigest() != record["golden_sha256"]:
        raise ValueError("Encrypted comparison checksum changed")
    name = "blueprint-restore-" + state()["id"] + "-" + uuid.uuid4().hex[:10]
    if image is None:
        locked = image_lock()["db"]
        image = locked["config_digest"]
        uid = locked.get("runtime_uid", uid)
    uid = int(uid)
    if uid not in {70, 999}:
        raise ValueError("Unsupported database runtime UID")
    # No network, published ports, host directories, plaintext dump files or Docker socket.
    docker(
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        "ai-blueprint-lab=" + state()["id"],
        "--network",
        "none",
        "--read-only",
        "--user",
        f"{uid}:{uid}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "96",
        "--log-driver",
        "none",
        "--tmpfs",
        f"/var/lib/postgresql/data:rw,size=256m,uid={uid},gid={uid},mode=0700",
        "--tmpfs",
        f"/var/run/postgresql:rw,size=8m,uid={uid},gid={uid}",
        "--tmpfs",
        "/tmp:rw,size=16m,mode=1777",  # noqa: S108 - isolated container tmpfs
        "-e",
        "POSTGRES_DB=platform",
        "-e",
        "POSTGRES_USER=platform_admin",
        "-e",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        image,
    )
    try:

        def ready():
            result = run(
                [
                    "docker",
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "platform_admin",
                    "-d",
                    "platform",
                ],
                check=False,
            )
            return result.returncode == 0

        wait(ready, seconds=60)
        run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "pg_restore",
                "-U",
                "platform_admin",
                "-d",
                "platform",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
            ],
            input=archive,
        )
        result = run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-U",
                "platform_admin",
                "-d",
                "platform",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                recovery.SNAPSHOT,
            ]
        )
        actual = recovery.golden_bytes(recovery.normalize_snapshot(json.loads(result.stdout)))
        if actual != golden:
            raise AssertionError("Restored collections, payloads, chunks or vectors differ")
        return {
            "encrypted_archive_authenticated": True,
            "full_knowledge_snapshot_equal": True,
            "network": "none",
            "storage": "disposable tmpfs",
            "physical_offsite": False,
        }
    finally:
        docker("rm", "--force", name)


def execute(job, name):
    if job not in JOBS:
        raise ValueError("Unknown scheduled job")
    AUTOMATION.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (AUTOMATION / "execution.lock").open("a") as lock:
        # Serialize backup/restore and synthetic writes, including manual invocations.
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = AUTOMATION / (job + ".json")
        previous = json.loads(path.read_text()) if path.exists() else {}
        started = time.time()
        receipt = {
            "job": job,
            "vm": name,
            "last_run": started,
            "success": False,
            "last_success": previous.get("last_success", 0),
        }
        save(path, receipt)
        try:
            if job == "workflow":
                workflow()
            elif job == "backup":
                record, _ = recovery.backup(name)
                receipt["archive_sha256"] = record["encrypted_sha256"]
            else:
                receipt["checks"] = restore()
            receipt.update(success=True, last_success=time.time())
        except Exception as error:
            receipt["error_class"] = type(error).__name__
            raise
        finally:
            receipt.update(
                duration_seconds=round(time.time() - started, 2),
                completed_utc=datetime.now(UTC).isoformat(),
            )
            save(path, receipt)
    print("Scheduled " + job + " passed; private receipt updated.")


def units(name):
    if name not in {"primary", "recovery"}:
        raise ValueError("Unknown owned VM")
    AUTOMATION.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = "ai-blueprint-" + state()["id"]
    paths = []
    for job, (calendar, _) in JOBS.items():
        stem = prefix + "-" + job
        command = [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/local-lab.py"),
            job + "-run",
            "--name",
            name,
        ]
        # systemd quoting differs from shell quoting; owned workspace path has no special chars.
        if any(any(c in arg for c in '\n\r%"\\') for arg in command):
            raise ValueError("Unsupported systemd path characters")
        quoted = " ".join('"' + arg + '"' for arg in command)
        content = (
            f"[Unit]\nDescription=Local platform {job} check\n"
            "[Service]\nType=oneshot\nUMask=0077\n"
            f"WorkingDirectory={ROOT}\nExecStart={quoted}\n"
            "TimeoutStartSec=10min\nMemoryMax=1G\n"
        )
        service = AUTOMATION / (stem + ".service")
        service.write_text(content)
        timer = AUTOMATION / (stem + ".timer")
        timer.write_text(
            f"[Unit]\nDescription=Local platform {job} schedule\n[Timer]\n"
            f"OnCalendar={calendar}\nPersistent=true\nAccuracySec=30s\n"
            f"Unit={stem}.service\n[Install]\nWantedBy=timers.target\n"
        )
        service.chmod(0o600)
        timer.chmod(0o600)
        paths += [service, timer]
    return paths


def install(name):
    paths = units(name)
    run(["systemctl", "--user", "link", *[str(path) for path in paths]])
    run(["systemctl", "--user", "daemon-reload"])
    run(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            *[path.name for path in paths if path.suffix == ".timer"],
        ]
    )
    print("Three project-owned user timers enabled; no linger/global settings changed.")
