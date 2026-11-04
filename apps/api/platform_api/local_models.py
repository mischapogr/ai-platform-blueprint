"""Native Ollama adapter. Only operator-configured loopback or Compose endpoints."""

import ipaddress
import json
import math
import os
import re
from urllib.parse import urlsplit

import httpx

from .models import ModelError

MAX_RESPONSE = 8 * 1024 * 1024
MAX_CONTEXT_BYTES = 6500


def endpoint():
    value = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
    parsed = urlsplit(value)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme != "http"
        or not (loopback or parsed.hostname in {"localhost", "ollama"})
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
        or parsed.port is None
    ):
        raise ModelError("Configure a local Ollama HTTP endpoint with an explicit port")
    return value


def configured():
    try:
        endpoint()
        return True
    except (ValueError, ModelError):
        return False


def request(path, payload=None):
    """No proxy, redirect, retry, remote fallback or response-body error echo."""
    try:
        base = endpoint()
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(180 if path == "/api/chat" else 60, connect=2),
        ) as client:
            with client.stream(
                "GET" if payload is None else "POST", base + path, json=payload
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > MAX_RESPONSE:
                        raise ModelError("Local model response exceeded its size limit")
        data = json.loads(body)
        if not isinstance(data, dict) or "error" in data:
            raise ModelError("Local model returned an invalid response")
        return data
    except (httpx.HTTPError, ValueError, TypeError):
        raise ModelError(
            "Local model request failed; check runtime, model and context limits"
        ) from None


def chat_models():
    return [
        m.strip() for m in os.getenv("OLLAMA_CHAT_MODELS", "llama3.2:3b").split(",") if m.strip()
    ]


def digest(model):
    installed = request("/api/tags").get("models")
    if not isinstance(installed, list):
        raise ModelError("Local runtime returned invalid model metadata")
    matches = [m for m in installed if isinstance(m, dict) and m.get("name") == model]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("digest"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", matches[0]["digest"])
    ):
        raise ModelError("Configured local model is not installed or has no valid digest")
    return matches[0]["digest"]


def identity(model, capability):
    model_digest = digest(model)
    info = request("/api/show", {"model": model})
    if not isinstance(info.get("capabilities"), list) or capability not in info["capabilities"]:
        raise ModelError("Local model lacks the required capability")
    if not isinstance(info.get("model_info"), dict):
        raise ModelError("Local model returned invalid capacity metadata")
    result = {"model": model, "digest": model_digest, "endpoint": endpoint()}
    if capability == "embedding":
        dims = [v for k, v in info.get("model_info", {}).items() if k.endswith(".embedding_length")]
        if len(dims) != 1 or type(dims[0]) is not int or not 1 <= dims[0] <= 4096:
            raise ModelError("Local embedding dimensions must be known and between 1 and 4096")
        result["dimensions"] = dims[0]
    else:
        contexts = [
            v for k, v in info.get("model_info", {}).items() if k.endswith(".context_length")
        ]
        if len(contexts) != 1 or type(contexts[0]) is not int or contexts[0] < 2048:
            raise ModelError("Local generation context capacity is unknown or too small")
        result["context_length"] = min(contexts[0], 8192)
    return result


def embedding_config():
    model = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:latest")
    profile = os.getenv("OLLAMA_EMBEDDING_PROFILE", "nomic-v1")
    if profile not in {"plain", "nomic-v1"}:
        raise ModelError("Unsupported local embedding profile")
    return {**identity(model, "embedding"), "profile": profile}


def token_count(data, field):
    value = data.get(field)
    if value is not None and (type(value) is not int or value < 0):
        raise ModelError("Local model returned invalid token usage")
    return value


def embeddings(texts, collection, purpose):
    config = embedding_config()
    expected = (
        collection["embedding_model"],
        collection.get("embedding_digest"),
        collection.get("embedding_endpoint"),
        collection["dimensions"],
        collection.get("embedding_profile"),
    )
    actual = tuple(config[k] for k in ("model", "digest", "endpoint", "dimensions", "profile"))
    if expected != actual:
        raise ModelError(
            "Local embedding configuration changed; create a new collection and reimport"
        )
    prefix = (
        ("search_query: " if purpose == "query" else "search_document: ")
        if config["profile"] == "nomic-v1"
        else ""
    )
    data = request(
        "/api/embed",
        {
            "model": config["model"],
            "input": [prefix + t for t in texts],
            "truncate": False,
            "keep_alive": "0s",
            "options": {"num_gpu": 0, "num_thread": 4},
        },
    )
    vectors = data.get("embeddings")
    if data.get("model") != config["model"]:
        raise ModelError("Local embedding response identifies a different model")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ModelError("Local embedding count does not match input count")
    for vector in vectors:
        if (
            not isinstance(vector, list)
            or len(vector) != config["dimensions"]
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
            or not any(v != 0 for v in vector)
        ):
            raise ModelError("Local model returned an invalid embedding vector")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ModelError("Local model returned an invalid embedding norm")
        vector[:] = [float(v) / norm for v in vector]
    if digest(config["model"]) != config["digest"]:
        raise ModelError("Local model changed during embedding; retry with a stable model")
    return vectors, token_count(data, "prompt_eval_count")


def generate(model, instructions, messages, *, schema=None):
    if model not in chat_models():
        raise ModelError("Local model is not enabled in this deployment")
    info = identity(model, "completion")
    byte_count = len(instructions.encode()) + sum(len(m["content"].encode()) for m in messages)
    if byte_count > min(MAX_CONTEXT_BYTES, info["context_length"] - 1024):
        raise ModelError(
            "Local generation context is too large; clear history or request fewer passages"
        )
    data = request(
        "/api/chat",
        {
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": instructions}, *messages],
            **({"format": schema} if schema is not None else {}),
            "options": {
                "num_ctx": info["context_length"],
                "num_predict": 384 if schema else 192,
                "temperature": 0,
                "seed": 17,
                "num_gpu": 0,
                "num_thread": 4,
            },
            "keep_alive": "0s",
        },
    )
    message = data.get("message", {})
    if (
        data.get("model") != model
        or data.get("done") is not True
        or not isinstance(message, dict)
        or message.get("tool_calls")
    ):
        raise ModelError("Local model did not return a completed text answer")
    answer = message.get("content")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
        raise ModelError("Local model returned an invalid answer")
    if digest(model) != info["digest"]:
        raise ModelError("Local model changed during generation; retry with a stable model")
    return answer, {
        "input_tokens": token_count(data, "prompt_eval_count"),
        "output_tokens": token_count(data, "eval_count"),
        "model_digest": info["digest"],
        "finish_reason": data.get("done_reason"),
    }
