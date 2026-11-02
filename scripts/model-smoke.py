#!/usr/bin/env python3
"""Optional real API request with synthetic input; invoked explicitly by the owner."""

import httpx
from common import ROOT

with httpx.Client(trust_env=False, base_url="http://127.0.0.1:8080", timeout=55) as api:
    api.post(
        "/api/session", json={"token": (ROOT / ".secrets/workspace_token").read_text().strip()}
    ).raise_for_status()
    status = api.get("/api/status").json()
    if not status["provider_configured"]:
        raise SystemExit("Configure a local provider key with make configure-openai first.")
    response = api.post(
        "/api/chat",
        json={
            "question": "Reply with exactly: connection ready.",
            "mode": "chat",
            "provider": "openai",
            "model": status["models"][0],
            "external_consent": True,
        },
    )
    response.raise_for_status()
    result = response.json()
    assert result["mode"] == "openai" and result["answer"]
    assert result["usage"]["input_tokens"] > 0
    print("Live model smoke passed; provider reported tokens:", result["usage"])
