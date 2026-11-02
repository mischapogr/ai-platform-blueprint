#!/usr/bin/env python3
"""Create local secrets once; never rotate credentials behind an existing volume."""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
secret_dir = root / ".secrets"
secret_dir.mkdir(mode=0o700, exist_ok=True)
for name in ("db_password", "app_password", "grafana_password", "workspace_token", "backup.key"):
    path = secret_dir / name
    if path.exists():
        continue
    value = Fernet.generate_key() if name == "backup.key" else secrets.token_hex(32).encode()
    # Compose mounts local secrets as files without remapping their UID/mode.
    # A private 0700 parent protects host access; non-root container users need read access.
    mode = 0o600 if name == "backup.key" else 0o644
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value + b"\n")
print("Local secret files ready; existing values preserved.")

# Empty placeholder is intentional; real provider credentials are supplied locally.
key_path = secret_dir / "openai_api_key"
if not key_path.exists():
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)

local_dir = root / ".local"
local_dir.mkdir(mode=0o700, exist_ok=True)
local_dir.chmod(0o700)
