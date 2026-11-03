"""Encrypted recovery across fresh local QEMU disks; not physical offsite recovery."""

import hashlib
import json
import os
import shlex
import shutil
import ssl
import time
import uuid
from datetime import UTC, datetime

import httpx
from cryptography.fernet import Fernet, InvalidToken

from . import acceptance, deploy, registry, vm
from .common import PRIVATE, ROOT, STATE, run, save, secret, state

MAX_DUMP = 64 * 1024 * 1024
SNAPSHOT = """SELECT json_build_object(
 'collections',(SELECT COALESCE(json_agg(c ORDER BY id),'[]'::json) FROM kb_collections c),
 'sources',(SELECT COALESCE(json_agg(s ORDER BY id),'[]'::json) FROM
  (SELECT id,collection_id,title,kind,locator,encode(payload,'hex') AS payload_hex,
    status,chunk_count FROM kb_sources) s),
 'chunks',(SELECT COALESCE(json_agg(ch ORDER BY id),'[]'::json) FROM
  (SELECT id,source_id,collection_id,content,page,locator,ordinal,embedding::text
   FROM kb_chunks) ch))"""


def snapshot(name):
    result = acceptance.command(
        name,
        "exec -T db psql -U platform_admin -d platform -At -v ON_ERROR_STOP=1 -c "
        + shlex.quote(SNAPSHOT),
    )
    value = json.loads(result.stdout)
    for source in value["sources"]:
        raw = source.pop("payload_hex")
        source["payload_sha256"] = hashlib.sha256(bytes.fromhex(raw or "")).hexdigest()
    return value


def terraform(name, operation):
    # The built-in terraform_data provider and local backend need no remote account.
    data = STATE / ("terraform-data-" + name)
    backend = STATE / "terraform" / (name + ".tfstate")
    backend.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_DATA_DIR": str(data), "CHECKPOINT_DISABLE": "1"}
    base = [
        shutil.which("terraform") or "/usr/bin/terraform",
        "-chdir=" + str(ROOT / "infra/terraform/local-lab"),
    ]
    run(base + ["init", "-input=false", "-backend-config=path=" + str(backend)], env=env)
    try:
        run(
            base
            + [
                operation,
                "-auto-approve",
                "-input=false",
                "-var=project_root=" + str(ROOT),
                "-var=vm_name=" + name,
            ],
            env=env,
            timeout=1200,
        )
    except Exception:
        # Terraform contains no credential values; keep diagnostics private for failed boots.
        raise


def decrypt(cipher, encrypted):
    if len(encrypted) > MAX_DUMP * 2:
        raise ValueError("Oversized local backup")
    dump = cipher.decrypt(bytes(encrypted))
    if not dump.startswith(b"PGDMP") or len(dump) > MAX_DUMP:
        raise ValueError("Invalid database archive")
    return dump


def store_client():
    return httpx.Client(
        base_url=f"https://localhost:{state()['registry_port']}",
        auth=("lab", secret("registry-password").read_text()),
        verify=ssl.create_default_context(cafile=str(PRIVATE / "ca.crt")),
        trust_env=False,
        timeout=60,
    )


def backup(name):
    escrow = PRIVATE / "escrow"
    escrow.mkdir(exist_ok=True, mode=0o700)
    key = escrow / "backup.key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
    cipher = Fernet(key.read_bytes())
    start = datetime.now(UTC)
    golden = snapshot(name)
    # Read at most 64 MiB in the guest before sending plaintext over encrypted SSH.
    code = (
        "import subprocess,sys; "
        "p=subprocess.Popen(['docker','compose','--project-name','blueprint-local','--file',"
        f"'{deploy.GUEST}/compose.yaml','exec','-T','db','pg_dump','-U','platform_admin',"
        "'-d','platform','--format=custom','--no-owner','--no-acl'],stdout=subprocess.PIPE); "
        f"data=p.stdout.read({MAX_DUMP + 1}); "
        f"p.kill() if len(data)>{MAX_DUMP} else None; "
        f"ok=p.wait()==0 and len(data)<={MAX_DUMP} and data.startswith(b'PGDMP'); "
        "sys.stdout.buffer.write(data) if ok else sys.exit(1)"
    )
    raw = vm.ssh(name, "sudo python3 -c " + shlex.quote(code), timeout=180).stdout
    encrypted = cipher.encrypt(raw)
    filename = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex + ".fernet"
    with store_client() as store:
        missing = store.get("/backup-store/missing-" + uuid.uuid4().hex + ".fernet")
        if missing.status_code != 404:
            raise AssertionError("Missing backup did not fail closed")
        store.put("/backup-store/" + filename, content=encrypted).raise_for_status()
        with store.stream("GET", "/backup-store/" + filename) as response:
            response.raise_for_status()
            downloaded = bytearray()
            for block in response.iter_bytes():
                downloaded.extend(block)
                if len(downloaded) > MAX_DUMP * 2:
                    raise ValueError("Oversized downloaded backup")
    if hashlib.sha256(downloaded).digest() != hashlib.sha256(encrypted).digest():
        raise AssertionError("Stored encrypted archive changed")
    decrypt(cipher, downloaded)
    negatives = {"missing_archive": "HTTP 404"}
    for label, other, payload in (
        ("wrong_key", Fernet(Fernet.generate_key()), downloaded),
        ("corrupt_archive", cipher, bytes(downloaded[:-5]) + b"xxxxx"),
    ):
        try:
            decrypt(other, payload)
        except (InvalidToken, ValueError):
            negatives[label] = "rejected before restore"
        else:
            raise AssertionError("Unauthenticated backup accepted")
    manifest_path = STATE / "backup-index.json"
    index = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    record = {
        "filename": filename,
        "started_utc": start.isoformat(),
        "completed_utc": datetime.now(UTC).isoformat(),
        "encrypted_sha256": hashlib.sha256(encrypted).hexdigest(),
        "key_sha256": hashlib.sha256(key.read_bytes()).hexdigest(),
        "negative_checks": negatives,
    }
    index.append(record)
    with store_client() as store:
        for old in index[:-3]:
            store.delete("/backup-store/" + old["filename"]).raise_for_status()
    save(manifest_path, index[-3:])
    if snapshot(name) != golden:
        raise RuntimeError("Source changed during backup; original guest retained")
    save(STATE / "backup-golden.json", golden)
    print(
        "Encrypted backup uploaded, downloaded, authenticated; wrong key/corruption rejected.",
        flush=True,
    )
    return record, golden


def drill():
    registry.verify()
    marker = json.loads((vm.directory("primary") / "metadata.json").read_text())
    if marker.get("destroyed") or marker["lab_id"] != state()["id"]:
        raise ValueError("Recovery requires this lab's running primary guest")
    if (vm.directory("recovery") / "disk.qcow2").exists():
        raise ValueError("Recovery guest already exists; preserving it")
    backup_record, golden = backup("primary")
    with acceptance.client() as api:
        lost_collection = acceptance.seed(api, "post-backup-write-expected-to-be-lost")
    incident = datetime.now(UTC)
    clock = time.monotonic()
    print("Removing only the disposable primary guest and its disk through Terraform.", flush=True)
    terraform("primary", "destroy")
    if (vm.directory("primary") / "disk.qcow2").exists():
        raise AssertionError("Original guest disk still exists")
    # Runtime credentials are regenerated; only independent registry/signing/key escrow survives.
    for runtime_secret in ("db_password", "app_password", "workspace_token"):
        (PRIVATE / runtime_secret).unlink()
        secret(runtime_secret)
    print("Creating and provisioning a fresh recovery guest disk.", flush=True)
    terraform("recovery", "apply")
    deploy.ansible("recovery", "provision")
    deploy.apply("recovery")
    # Decrypt after source destruction using only controller escrow and external store.
    with store_client() as store:
        response = store.get("/backup-store/" + backup_record["filename"])
        response.raise_for_status()
        encrypted = response.content
    if hashlib.sha256(encrypted).hexdigest() != backup_record["encrypted_sha256"]:
        raise ValueError("Recovery archive checksum changed")
    dump = decrypt(Fernet((PRIVATE / "escrow/backup.key").read_bytes()), encrypted)
    acceptance.command("recovery", "stop api worker")
    acceptance.command(
        "recovery",
        "exec -T db pg_restore -U platform_admin -d platform --clean --if-exists "
        "--no-owner --no-acl --exit-on-error",
        input=dump,
        timeout=180,
    )
    acceptance.command(
        "recovery",
        "exec -T db psql -U platform_admin -d platform -v ON_ERROR_STOP=1 "
        "-c 'GRANT SELECT, INSERT ON documents TO platform_app'",
    )
    for migration in sorted((ROOT / "infra/compose/postgres").glob("[0-9]*.sql")):
        acceptance.command(
            "recovery",
            "exec -T db psql -U platform_admin -d platform "
            "-v ON_ERROR_STOP=1 -f /migrations/" + migration.name,
        )
    acceptance.command("recovery", "up --detach --wait api worker")
    if snapshot("recovery") != golden:
        raise AssertionError("Restored collections/payload hashes/chunks/vectors differ")
    with acceptance.client() as api:
        current = api.get("/api/collections")
        current.raise_for_status()
        if lost_collection in {row["id"] for row in current.json()}:
            raise AssertionError("Post-backup write survived unexpectedly")
        acceptance.query(api, golden["collections"][0]["id"])
        acceptance.query(api, acceptance.seed(api, "post-restore-ingestion"))
    receipt = {
        "scope": (
            "same physical host; separate registry store/controller key escrow/fresh QEMU guest"
        ),
        "backup": backup_record,
        "original_disk_removed": True,
        "fresh_runtime_credentials": True,
        "golden_collections_payload_hashes_chunks_vectors_equal": True,
        "post_backup_write_absent": True,
        "restored_chat_and_new_ingestion": True,
        "rpo_seconds": (
            incident - datetime.fromisoformat(backup_record["started_utc"])
        ).total_seconds(),
        "rto_seconds": round(time.monotonic() - clock, 2),
        "recovered_utc": datetime.now(UTC).isoformat(),
        "physical_offsite": False,
    }
    save(STATE / "recovery-receipt.json", receipt)
    print("Fresh local VM recovery passed; measured RPO/RTO recorded.", flush=True)
    return receipt
