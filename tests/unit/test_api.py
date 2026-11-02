import json

import psycopg
from fastapi.testclient import TestClient
from platform_api import main


def test_database_failure_is_bounded_and_does_not_break_liveness(monkeypatch, tmp_path):
    def fail():
        raise psycopg.OperationalError("secret-must-not-escape")

    monkeypatch.setattr(main.db, "connect", fail)
    monkeypatch.setenv("REQUEST_LOG", str(tmp_path / "requests.jsonl"))
    with TestClient(main.app) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert "secret-must-not-escape" not in response.text
        assert client.post("/query", json={"question": "backup"}).status_code == 503
        assert (
            client.post("/documents", json={"title": "Backup", "content": "Restore"}).status_code
            == 503
        )
        assert "platform_database_up 0.0" in client.get("/metrics").text


def test_input_bounds_and_logs_do_not_leak_raw_paths_or_bodies(monkeypatch, tmp_path):
    path = tmp_path / "requests.jsonl"
    monkeypatch.setenv("REQUEST_LOG", str(path))
    with TestClient(main.app) as client:
        for payload in [{"question": "?"}, {"question": "x", "top_k": 6}, {"question": "x" * 2001}]:
            assert client.post("/query", json=payload).status_code == 422
        assert client.post("/documents", json={"title": " ", "content": "test"}).status_code == 422
        assert client.get("/private-customer-name?token=secret").status_code == 404
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert entries[-1]["route"] == "unmatched"
    assert "private-customer-name" not in path.read_text()
    assert "secret" not in path.read_text()
