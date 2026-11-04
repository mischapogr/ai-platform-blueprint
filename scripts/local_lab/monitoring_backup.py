"""Encrypt a consistent Grafana SQLite snapshot before a major-version update."""

import hashlib
import re
import shlex

from cryptography.fernet import Fernet

from . import acceptance, vm
from .common import PRIVATE, STATE, save


def grafana(name):
    container = acceptance.command(name, "ps -q grafana").stdout.decode().strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise ValueError("Unknown Grafana container")
    source = (
        vm.ssh(
            name,
            "sudo docker inspect --format "
            + shlex.quote(
                '{{range .Mounts}}{{if eq .Destination "/var/lib/grafana"}}'
                "{{.Source}}{{end}}{{end}}"
            )
            + " "
            + container,
        )
        .stdout.decode()
        .strip()
    )
    if source != "/var/lib/docker/volumes/blueprint-local_grafana/_data":
        raise ValueError("Grafana data is outside this deployment's volume")
    code = (
        "import sqlite3,sys; "
        "source=sqlite3.connect('file:" + source + "/grafana.db?mode=ro',uri=True); "
        "target=sqlite3.connect(':memory:'); source.backup(target); data=target.serialize(); "
        "assert len(data)<32*1024*1024 and data.startswith(b'SQLite format 3'); "
        "sys.stdout.buffer.write(data)"
    )
    data = vm.ssh(name, "sudo python3 -c " + shlex.quote(code)).stdout
    cipher = Fernet((PRIVATE / "escrow/backup.key").read_bytes())
    encrypted = cipher.encrypt(data)
    if cipher.decrypt(encrypted) != data:
        raise ValueError("Grafana snapshot authentication failed")
    path = STATE / "grafana-before-upgrade.fernet"
    path.write_bytes(encrypted)
    path.chmod(0o600)
    save(
        STATE / "grafana-before-upgrade.json",
        {
            "encrypted_sha256": hashlib.sha256(encrypted).hexdigest(),
            "scope": "Grafana SQLite only; local encrypted snapshot; no monitoring-volume DR claim",
        },
    )
    print("Consistent Grafana SQLite snapshot encrypted and authenticated.")
