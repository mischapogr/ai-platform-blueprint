#!/usr/bin/env python3
import json

import httpx
from common import ROOT

with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=10) as client:
    for document in json.loads((ROOT / "apps/demo-rag/documents.json").read_text()):
        response = client.post("/documents", json=document)
        response.raise_for_status()
print("Three synthetic documents seeded (idempotent).")
