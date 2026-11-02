#!/usr/bin/env python3
"""Import a private local manifest. The repository contains no real source manifest."""

import argparse
import json
from pathlib import Path

import httpx
from common import ROOT, wait_until

parser = argparse.ArgumentParser()
parser.add_argument("--manifest", required=True, type=Path)
args = parser.parse_args()
manifest = json.loads(args.manifest.read_text())
with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=20) as api:
    api.post(
        "/api/session", json={"token": (ROOT / ".secrets/workspace_token").read_text().strip()}
    ).raise_for_status()
    for area in manifest["collections"]:
        existing = api.get("/api/collections").json()
        matches = [c for c in existing if c["name"] == area["name"]]
        if len(matches) > 1:
            raise SystemExit("Ambiguous collection name; choose a unique collection name.")
        collection = matches[0] if matches else None
        if collection and (
            collection["embedding_provider"] != area.get("embedding_provider", "offline")
            or collection["allow_external"] != area.get("allow_external", False)
        ):
            raise SystemExit(
                "Collection processing policy differs from manifest; choose a new collection."
            )
        if collection is None:
            response = api.post(
                "/api/collections",
                json={
                    key: area[key]
                    for key in ["name", "description", "embedding_provider", "allow_external"]
                    if key in area
                },
            )
            response.raise_for_status()
            collection = response.json()
        for source in area["sources"]:
            present = api.get("/api/collections/" + collection["id"] + "/sources").json()
            if any(
                row["locator"] == source.get("url") and row["status"] == "ready" for row in present
            ):
                print("Source already indexed; skipped.")
                continue
            response = api.post("/api/collections/" + collection["id"] + "/sources", json=source)
            response.raise_for_status()
            source_id = response.json()["id"]
            final = {}

            def finished(collection_id=collection["id"], expected_id=source_id, result=final):
                rows = api.get("/api/collections/" + collection_id + "/sources").json()
                row = next(row for row in rows if row["id"] == expected_id)
                result.update(row)
                return row["status"] in ["ready", "failed"]

            wait_until(finished, timeout=300)
            if final["status"] != "ready":
                raise SystemExit("Source ingestion failed: " + str(final["error_code"]))
            print(
                f"Source indexed: {final['chunk_count']} passages. "
                "Original content stays in local storage."
            )
