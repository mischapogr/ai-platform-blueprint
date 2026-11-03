import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from platform_api import knowledge, local_models, models

DIGEST = "a" * 64


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:latest")
    monkeypatch.setenv("OLLAMA_EMBEDDING_PROFILE", "nomic-v1")
    seen = []
    result = {
        "embed": {"model": "nomic-embed-text:latest", "embeddings": [[0.6, 0.8]]},
        "chat": {
            "model": "llama3.2:3b",
            "done": True,
            "message": {"content": "Supported fact [1]."},
        },
    }

    def respond(request):
        data = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, data))
        if request.url.path == "/api/tags":
            body = {
                "models": [
                    {"name": n, "digest": result.get("digest", DIGEST)}
                    for n in ("nomic-embed-text:latest", "llama3.2:3b")
                ]
            }
        elif request.url.path == "/api/show":
            body = {
                "capabilities": ["embedding", "completion"],
                "model_info": {"test.embedding_length": 2, "test.context_length": 8192},
            }
        else:
            body = result["embed" if request.url.path == "/api/embed" else "chat"]
            if result.get("retag_during_call"):
                result["digest"] = "b" * 64
        return httpx.Response(200, json=body)

    client = httpx.Client
    monkeypatch.setattr(
        local_models.httpx,
        "Client",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(models, "record_usage", lambda *args: None)
    config = local_models.embedding_config()
    collection = {
        "id": "synthetic",
        "allow_external": False,
        "embedding_provider": "ollama",
        "embedding_model": config["model"],
        "dimensions": 2,
        "embedding_endpoint": config["endpoint"],
        "embedding_digest": DIGEST,
        "embedding_profile": config["profile"],
    }
    return collection, seen, result


def test_local_only_embeddings_use_query_profile_and_report_missing_usage(runtime):
    collection, seen, _ = runtime
    assert models.embeddings(["synthetic query"], collection, purpose="query") == [[0.6, 0.8]]
    payload = next(data for path, data in seen if path == "/api/embed")
    assert payload["truncate"] is False and payload["input"] == ["search_query: synthetic query"]
    answer, usage = models.generate(
        "question",
        [],
        [{"title": "t", "content": "fact"}],
        collection,
        "llama3.2:3b",
        provider="ollama",
    )
    assert answer == "Supported fact [1]."
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
    payload = next(data for path, data in seen if path == "/api/chat")
    assert payload["stream"] is False and "tools" not in payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("embedding_digest", "b" * 64),
        ("embedding_endpoint", "http://127.0.0.1:11436"),
        ("dimensions", 3),
        ("embedding_profile", "plain"),
    ],
)
def test_embedding_identity_changes_require_reimport(runtime, field, value):
    collection, seen, _ = runtime
    collection = {**collection, field: value}
    with pytest.raises(models.ModelError, match="new collection"):
        models.embeddings(["fact"], collection)
    assert not any(path == "/api/embed" for path, _ in seen)


@pytest.mark.parametrize("vectors", [[], [[0, 0]], [[0.5]], [[float("nan"), 1]], [[True, 1]]])
def test_malformed_embeddings_fail(runtime, vectors):
    collection, _, result = runtime
    result["embed"]["embeddings"] = vectors
    with pytest.raises(models.ModelError):
        models.embeddings(["fact"], collection)


def test_context_limit_and_disallowed_models_fail_before_generation(runtime):
    collection, seen, _ = runtime
    for text, model in (("x" * 20000, "llama3.2:3b"), ("question", "unapproved-model")):
        with pytest.raises(models.ModelError):
            models.generate(text, [], [], collection, model, provider="ollama")
    assert not any(path == "/api/chat" for path, _ in seen)


def test_retag_during_inference_is_rejected_and_vectors_are_normalized(runtime):
    collection, _, result = runtime
    result["embed"]["embeddings"] = [[3, 4]]
    assert models.embeddings(["fact"], collection) == [[0.6, 0.8]]
    result["retag_during_call"] = True
    with pytest.raises(models.ModelError, match="changed during"):
        models.embeddings(["fact"], collection)


def test_local_endpoint_policy_and_safe_errors(monkeypatch):
    for endpoint in (
        "https://external.example:443",
        "http://10.0.0.1:11434",
        "http://localhost:11434/other",
        "http://user:pass@localhost:11434",
    ):
        monkeypatch.setenv("OLLAMA_BASE_URL", endpoint)
        assert not local_models.configured()
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")

    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("SYNTHETIC-PRIVATE-PROVIDER-ERROR")

    monkeypatch.setattr(local_models.httpx, "Client", fail)
    with pytest.raises(models.ModelError) as error:
        local_models.request("/api/embed", {"input": "synthetic"})
    assert "PRIVATE" not in str(error.value)


def test_local_collection_cannot_send_external_answers(monkeypatch, runtime):
    collection, _, _ = runtime
    monkeypatch.setattr(knowledge, "get_collection", lambda _: collection)
    with pytest.raises(HTTPException) as error:
        knowledge.chat(
            knowledge.ChatInput(
                question="fact", collection_id=uuid4(), provider="openai", external_consent=True
            )
        )
    assert error.value.status_code == 403
