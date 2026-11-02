from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from platform_api import knowledge, main, models


def test_workspace_auth_and_no_credential_echo(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("test-workspace-access-key")
    monkeypatch.setenv("WORKSPACE_TOKEN_FILE", str(token))
    monkeypatch.setenv("REQUEST_LOG", str(tmp_path / "requests.jsonl"))
    with TestClient(main.app) as client:
        assert client.get("/api/collections").status_code == 401
        assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
        assert (
            client.post(
                "/api/session",
                headers={"Origin": "https://attacker.example"},
                json={"token": token.read_text()},
            ).status_code
            == 403
        )
        result = client.post("/api/session", json={"token": token.read_text()})
        assert result.status_code == 200
        assert token.read_text() not in result.text
        assert "HttpOnly" in result.headers["set-cookie"]
        assert "SameSite=strict" in result.headers["set-cookie"]
    assert token.read_text() not in (tmp_path / "requests.jsonl").read_text()


def test_collection_policy_rejects_before_retrieval_or_provider(monkeypatch):
    collection = {"id": uuid4(), "allow_external": False, "embedding_provider": "offline"}
    monkeypatch.setattr(knowledge, "get_collection", lambda _: collection)
    monkeypatch.setattr(models, "key_available", lambda: True)

    def forbidden(*args):
        raise AssertionError("must not call retrieval/provider")

    monkeypatch.setattr(knowledge, "retrieve", forbidden)
    with pytest.raises(HTTPException) as error:
        knowledge.chat(
            knowledge.ChatInput(
                question="private context",
                collection_id=collection["id"],
                provider="openai",
                external_consent=True,
            )
        )
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        knowledge.chat(
            knowledge.ChatInput(
                question="private context", collection_id=collection["id"], provider="openai"
            )
        )
    assert error.value.status_code == 400


def test_openai_adapter_sends_bounded_stateless_request(monkeypatch):
    captured = {}

    @contextmanager
    def sdk():
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text="Supported answer [1]",
                usage=SimpleNamespace(input_tokens=12, output_tokens=8),
            )

        yield SimpleNamespace(responses=SimpleNamespace(create=create))

    monkeypatch.setattr(models, "client", sdk)
    monkeypatch.setattr(models, "record_usage", lambda *args: None)
    collection = {"id": uuid4(), "allow_external": True}
    answer, usage = models.generate(
        "Question",
        [],
        [{"title": "Reference", "content": "Untrusted passage"}],
        collection,
        "gpt-6-luna",
    )
    assert captured["store"] is False
    assert captured["max_output_tokens"] == 1600
    assert "tools" not in captured
    assert "untrusted data" in captured["instructions"]
    assert answer == "Supported answer [1]" and usage["input_tokens"] == 12
