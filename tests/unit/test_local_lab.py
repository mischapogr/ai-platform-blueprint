"""Local lab boundaries: URL authentication, archive names and authenticated recovery."""

import base64
import importlib
import io
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
server = importlib.import_module("local_lab.artifact_server")
recovery = importlib.import_module("local_lab.recovery")
vm = importlib.import_module("local_lab.vm")


def handler(path):
    instance = object.__new__(server.Server)
    instance.path = path
    return instance


def test_auth_query_is_accepted_without_exposing_backup_files(monkeypatch):
    instance = handler("/auth?_state=registry-upload-state")
    instance.headers = {"Authorization": "Basic " + base64.b64encode(b"lab:test-key").decode()}
    monkeypatch.setattr(server.Path, "read_bytes", lambda _: b"test-key\n")
    codes = []
    instance.respond = codes.append
    instance.do_GET()
    assert codes == [200]
    instance.headers = {"Authorization": "Basic invalid-base64"}
    assert not instance.authorized()


@pytest.mark.parametrize(
    "path",
    [
        "/backup-store/../secret.fernet",
        "/backup-store/%2e%2e.fernet",
        "/backup-store/a/b.fernet",
        "/backup-store/key.pem",
        "/etc/passwd",
    ],
)
def test_backup_store_refuses_unapproved_archive_paths(path):
    assert handler(path).archive() is None


def test_backup_authentication_fails_before_restore():
    cipher = Fernet(Fernet.generate_key())
    archive = cipher.encrypt(b"PGDMPsynthetic-test")
    assert recovery.decrypt(cipher, archive) == b"PGDMPsynthetic-test"
    with pytest.raises(InvalidToken):
        recovery.decrypt(Fernet(Fernet.generate_key()), archive)
    with pytest.raises(InvalidToken):
        recovery.decrypt(cipher, archive[:-2] + b"xx")
    with pytest.raises(ValueError, match="Invalid"):
        recovery.decrypt(cipher, cipher.encrypt(b"not a PostgreSQL dump"))


def test_vm_name_refuses_path_escape():
    with pytest.raises(ValueError, match="Invalid"):
        vm.directory("../existing-user-vm")


def test_truncated_archive_does_not_replace_existing_backup(monkeypatch, tmp_path):
    target = tmp_path / "synthetic.fernet"
    instance = handler("/backup-store/synthetic.fernet")
    instance.headers = {"Content-Length": "100"}
    instance.rfile = io.BytesIO(b"truncated")
    instance.authorized = lambda: True
    instance.archive = lambda: target
    codes = []
    instance.respond = codes.append
    instance.do_PUT()
    assert codes == [400]
    assert list(tmp_path.iterdir()) == []
    target.write_bytes(b"existing ciphertext")
    instance.do_PUT()
    assert codes[-1] == 409
    assert target.read_bytes() == b"existing ciphertext"


def test_proxy_diagnostics_drop_sensitive_fields():
    diagnostics = importlib.import_module("local_lab.diagnostics")
    import json

    raw = json.dumps(
        {
            "RouterName": "upload@file",
            "ServiceName": "api@file",
            "DownstreamStatus": 413,
            "OriginStatus": 0,
            "RequestPath": "/private-document",
            "Authorization": "secret-canary",
            "request_Cookie": "session-canary",
            "body": "private-pdf-content",
        }
    )
    result = diagnostics.proxy_events("proxy-1 | " + raw)
    assert result == [
        {
            "DownstreamStatus": 413,
            "OriginStatus": 0,
            "RouterName": "upload@file",
            "ServiceName": "api@file",
        }
    ]
    assert "canary" not in json.dumps(result) and "private" not in json.dumps(result)


def test_guest_upload_routing_and_observability_boundaries(monkeypatch, tmp_path):
    import json

    deploy = importlib.import_module("local_lab.deploy")
    observe = importlib.import_module("local_lab.observability")
    improve = importlib.import_module("local_lab.improvements")
    private = tmp_path / "private"
    private.mkdir()
    for name in ("registry.crt", "registry.key", "ca.crt"):
        (private / name).write_text("synthetic TLS fixture")

    def secret_fixture(name):
        path = private / name
        path.write_text("synthetic-config-secret")
        return path

    monkeypatch.setattr(deploy, "PRIVATE", private)
    monkeypatch.setattr(deploy, "STATE", tmp_path / "state")
    monkeypatch.setattr(deploy, "secret", secret_fixture)
    monkeypatch.setattr(observe, "secret", secret_fixture)
    monkeypatch.setattr(improve, "secret", secret_fixture)
    monkeypatch.setattr(improve, "state", lambda: {"probe_port": 19115, "registry_port": 15443})
    monkeypatch.setattr(deploy, "state", lambda: {"https_port": 18443, "registry_port": 15443})
    names = (
        "api",
        "db",
        "redis",
        "proxy",
        "prometheus",
        "alertmanager",
        "node",
        "grafana",
        "loki",
        "alloy",
        "tempo",
        "postgres_exporter",
        "redis_exporter",
    )
    monkeypatch.setattr(
        deploy.registry,
        "verify",
        lambda: {"images": {name: {"reference": name + "@sha256:" + "a" * 64} for name in names}},
    )
    target = deploy.prepare()
    routes = json.loads((target / "proxy/routes.yaml").read_text())
    routers = routes["http"]["routers"]
    # Prevent a long broad rule from silently outranking the upload limit.
    assert routers["upload"]["priority"] > (
        routers["workspace"].get("priority") or len(routers["workspace"]["rule"])
    )
    assert routes["http"]["middlewares"]["limit"]["buffering"]["maxRequestBodyBytes"] == 65536
    assert (
        routes["http"]["middlewares"]["upload-limit"]["buffering"]["maxRequestBodyBytes"]
        == 11000000
    )
    config = json.loads((target / "compose.yaml").read_text())
    proxy = config["services"]["proxy"]
    assert proxy["read_only"] and any(path.startswith("/" + "tmp:") for path in proxy["tmpfs"])
    assert "--accesslog.fields.defaultmode=drop" in proxy["command"]
    assert "--accesslog.fields.headers.defaultmode=drop" in proxy["command"]
    assert not any("RequestPath=" in arg for arg in proxy["command"])
    for name in ("grafana", "loki", "tempo", "alloy"):
        assert "ports" not in config["services"][name]
    assert "PLATFORM_TRACE_ENDPOINT" in config["services"]["api"]["environment"]
    assert "PLATFORM_TRACE_ENDPOINT" in config["services"]["worker"]["environment"]
    assert "grafana" in routers


def test_alert_receiver_keeps_only_known_component_metadata(monkeypatch, tmp_path):
    import json

    output = tmp_path / "events.jsonl"
    monkeypatch.setattr(
        server, "Path", lambda value: output if value == "/data/events.jsonl" else Path(value)
    )
    payload = json.dumps(
        {
            "alerts": [
                {
                    "labels": {
                        "alertname": "MonitoringTargetUnavailable",
                        "job": "tempo",
                        "private": "secret-canary",
                    },
                    "annotations": {"description": "private-source-content"},
                    "status": "firing",
                }
            ]
        }
    ).encode()
    instance = handler("/lab-alerts")
    instance.authorized = lambda: True
    instance.headers = {"Content-Length": str(len(payload))}
    instance.rfile = io.BytesIO(payload)
    codes = []
    instance.respond = codes.append
    instance.do_POST()
    assert codes == [204]
    row = json.loads(output.read_text())
    assert row["component"] == "tempo" and row["status"] == "firing"
    assert "canary" not in output.read_text() and "private" not in output.read_text()


def test_failed_scheduled_check_keeps_previous_success(monkeypatch, tmp_path):
    import json

    automation = importlib.import_module("local_lab.automation")
    monkeypatch.setattr(automation, "AUTOMATION", tmp_path)
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({"last_success": 123.0, "success": True}))

    def failing_workflow():
        raise RuntimeError("synthetic-private-error-must-not-be-in-receipt")

    monkeypatch.setattr(automation, "workflow", failing_workflow)
    with pytest.raises(RuntimeError):
        automation.execute("workflow", "recovery")
    record = json.loads(path.read_text())
    assert record["last_success"] == 123.0
    assert record["success"] is False
    assert record["error_class"] == "RuntimeError"
    assert "private-error" not in path.read_text()


def test_operator_metrics_are_numeric_and_content_free(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(server, "AUTOMATION_DIR", tmp_path)
    (tmp_path / "workflow.json").write_text(
        json.dumps(
            {
                "last_run": 100,
                "last_success": 99,
                "duration_seconds": 1,
                "success": True,
                "error_class": "private-canary",
                "document": "private-content-canary",
            }
        )
    )
    (tmp_path / "backup.json").write_text('{"last_success": NaN}')
    output = server.operator_metrics()
    assert 'blueprint_automation_success{check="workflow"} 1' in output
    assert 'blueprint_automation_last_success{check="backup"} 0' in output
    assert 'blueprint_automation_success{check="restore"} 0' in output
    assert "canary" not in output and "NaN" not in output


def test_resource_sampler_uses_cgroup_values_and_rejects_arbitrary_labels(tmp_path):
    resource = importlib.import_module("local_lab.guest_resources")
    group = tmp_path / "docker-abc.scope"
    group.mkdir()
    for name, content in {
        "memory.current": "1234",
        "memory.max": "4096",
        "cpu.stat": "nr_throttled 7\nthrottled_usec 900\n",
        "memory.events": "oom_kill 2\n",
    }.items():
        (group / name).write_text(content)
    output = resource.metrics(
        [
            {"Service": "worker", "ID": "abc", "State": "running"},
            {"Service": 'private"label', "ID": "bad", "State": "running"},
        ],
        {"abc": {"CPUPerc": "4.25%"}},
        tmp_path,
    )
    assert 'blueprint_container_memory_bytes{service="worker"} 1234' in output
    assert 'blueprint_container_cpu_throttled_periods_total{service="worker"} 7' in output
    assert 'blueprint_container_oom_kills_total{service="worker"} 2' in output
    assert "private" not in output


def test_qualified_release_requires_exact_complete_clean_runtime_inventory():
    import copy

    security = importlib.import_module("local_lab.runtime_security")
    expected = {"api": "sha256:api", "db": "sha256:db"}
    receipt = {
        "full_inventory": True,
        "expected_images": sorted(expected),
        "images": {
            name: {"image_config_digest": digest, "HIGH": 0, "CRITICAL": 0, "passed": True}
            for name, digest in expected.items()
        },
    }
    security.require_clean_receipt(receipt, expected)
    for mutate in (
        lambda r: r.update(full_inventory=False),
        lambda r: r["images"].pop("db"),
        lambda r: r["images"]["db"].update(image_config_digest="sha256:other"),
        lambda r: r["images"]["api"].update(HIGH=1),
        lambda r: r["images"]["api"].update(passed=False),
    ):
        altered = copy.deepcopy(receipt)
        mutate(altered)
        with pytest.raises(ValueError):
            security.require_clean_receipt(altered, expected)


def test_model_profile_is_private_cpu_only_and_optional():
    import copy

    profile = importlib.import_module("local_lab.model_profile")
    config = {"services": {name: {"environment": {}} for name in ("api", "worker")}}
    original = copy.deepcopy(config)
    profile.configure(config, {}, {})
    assert config == original
    profile.configure(config, {"ollama": "reviewed-model-image"}, {"models_enabled": True})
    model = config["services"]["ollama"]
    assert model["networks"] == ["backend"] and "ports" not in model
    assert model["cap_drop"] == ["ALL"] and model["read_only"] is True
    assert model["environment"]["OLLAMA_NO_CLOUD"] == "1"
    assert model["environment"]["OLLAMA_NOPRUNE"] == "1"
    assert model["environment"]["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert model["volumes"] == ["./model-cache:/models:ro"]
    assert config["services"]["worker"]["environment"]["OLLAMA_BASE_URL"] == "http://ollama:11434"


def test_controller_protects_artifacts_and_registry_with_the_same_auth_boundary():
    controller = importlib.import_module("local_lab.controller")
    config = controller.configuration()
    for name in ("registry", "artifacts"):
        router = config["http"]["routers"][name]
        assert router["middlewares"] == ["owner-auth"]
        assert router["tls"] == {}
    auth = config["http"]["middlewares"]["owner-auth"]["forwardAuth"]
    assert auth["authRequestHeaders"] == ["Authorization"]
    assert auth["forwardBody"] is False and auth["trustForwardHeader"] is False


@pytest.mark.parametrize(
    "budget",
    [{"vm_memory_mib": 0}, {"vm_memory_mib": True}, {"vm_memory_mib": 65536}, {"vm_cpus": 64}],
)
def test_guest_refuses_unbounded_resource_configuration(monkeypatch, budget):
    monkeypatch.setattr(vm, "state", lambda: budget)
    with pytest.raises(ValueError, match="RAM/CPU"):
        vm.create("recovery")


def test_tunnel_ownership_rejects_stale_or_unrelated_pids(tmp_path):
    deploy = importlib.import_module("local_lab.deploy")
    arguments = [
        b"/usr/bin/ssh",
        b"-N",
        b"-R",
        b"forward",
        ("UserKnownHostsFile=" + str(tmp_path / "known_hosts")).encode(),
        str(deploy.PRIVATE / "lab_ssh").encode(),
        b"lab@127.0.0.1",
    ]
    assert deploy.owns_tunnel(b"\0".join(arguments), tmp_path)
    assert not deploy.owns_tunnel(b"", tmp_path)
    assert not deploy.owns_tunnel(
        b"/usr/bin/other\0" + str(tmp_path / "known_hosts").encode(), tmp_path
    )
    assert not deploy.owns_tunnel(b"\0".join([b"/usr/bin/cat", *arguments[1:]]), tmp_path)
