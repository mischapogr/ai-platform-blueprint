#!/usr/bin/env python3
"""Interactive local setup; never echo, return or commit a real API credential."""

import getpass
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".secrets/openai_api_key"
path.parent.mkdir(mode=0o700, exist_ok=True)
key = getpass.getpass("OpenAI project API key (hidden; empty cancels): ").strip()
if not key:
    raise SystemExit("Unchanged")
if len(key) < 20 or any(c.isspace() for c in key):
    raise SystemExit("Invalid key format")
# Write the existing inode so a Compose secret bind sees updates without recreation.
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.fchmod(fd, 0o644)
with os.fdopen(fd, "w") as stream:
    stream.write(key + "\n")
print("Local key saved. It is ignored by Git and never returned by the app.")
