#!/usr/bin/env python3
import json

import httpx
from common import ROOT

with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=10) as client:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    document = json.loads((ROOT / "apps/demo-rag/documents.json").read_text())[0]
    result = client.post(
        "/query", json={"question": document["title"] + " " + document["content"], "top_k": 1}
    )
    result.raise_for_status()
    data = result.json()
    assert data["sources"][0]["title"] == "PostgreSQL backup recovery", data
    assert data["provider_calls"] == 0
    assert "isolated" in data["answer"]
print("Smoke passed: ready API and expected retrieval source.")
