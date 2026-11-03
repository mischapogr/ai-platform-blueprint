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
