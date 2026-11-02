import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx

DOCKER = shutil.which("docker") or "/usr/bin/docker"
ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [str(ROOT / "scripts/compose.sh")]
SNAPSHOT_SQL = """SELECT COALESCE(json_agg(d ORDER BY id), '[]'::json) FROM
(SELECT id, title, content, embedding::text FROM documents) d"""


def run(args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, **kwargs)  # noqa: S603


def snapshot():
    result = run(
        COMPOSE
        + [
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "platform_admin",
            "-d",
            "platform",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            SNAPSHOT_SQL,
        ],
        capture_output=True,
    )
    return json.loads(result.stdout)


def wait_until(check, timeout=90, interval=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, ValueError, IndexError, httpx.HTTPError):
            pass
        time.sleep(interval)
    raise AssertionError("Timed out waiting for expected state")


KNOWLEDGE_SQL = """
SELECT json_build_object(
  'collections', (SELECT COALESCE(json_agg(c ORDER BY id),'[]'::json) FROM kb_collections c),
  'sources', (SELECT COALESCE(json_agg(s ORDER BY id),'[]'::json) FROM
    (SELECT id,collection_id,title,kind,locator,md5(payload) AS payload_hash,status,chunk_count
     FROM kb_sources) s),
  'chunks', (SELECT COALESCE(json_agg(ch ORDER BY id),'[]'::json) FROM
    (SELECT id,source_id,collection_id,content,page,locator,ordinal,embedding::text
     FROM kb_chunks) ch)
)
"""


def knowledge_snapshot():
    result = run(
        COMPOSE
        + [
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "platform_admin",
            "-d",
            "platform",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            KNOWLEDGE_SQL,
        ],
        capture_output=True,
    )
    return json.loads(result.stdout)
