#!/usr/bin/env python3
"""Small liveness concurrency smoke; not a capacity benchmark or model load test."""

from concurrent.futures import ThreadPoolExecutor
from time import perf_counter

import httpx


def request(_):
    with httpx.Client(trust_env=False, timeout=5) as client:
        started = perf_counter()
        response = client.get("http://127.0.0.1:8080/health/live")
        response.raise_for_status()
        return perf_counter() - started


with ThreadPoolExecutor(max_workers=4) as pool:
    latencies = sorted(pool.map(request, range(20)))
print(
    f"20 local liveness requests passed; observed maximum {max(latencies):.3f}s. No capacity claim."
)
