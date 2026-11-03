import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_artifacts", Path(__file__).resolve().parents[2] / "scripts/release-artifacts.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_bundle_integrity_rejects_mutation_and_symlinks(tmp_path):
    for name in release.FILES:
        (tmp_path / name).write_text("synthetic public artifact")
    manifest = {
        "version": 1,
        "sha256": {name: release.digest(tmp_path / name) for name in release.FILES},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert release.verify(tmp_path) == manifest
    (tmp_path / "image.tar").write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        release.verify(tmp_path)
    (tmp_path / "image.tar").unlink()
    (tmp_path / "image.tar").symlink_to(tmp_path / "LICENSE")
    with pytest.raises(ValueError, match="integrity"):
        release.verify(tmp_path)


def test_rollback_gate_rejects_persisted_unsupported_providers():
    release.ensure_compatible(["offline"], ["offline", "openai"])
    with pytest.raises(ValueError, match="ollama"):
        release.ensure_compatible(["offline", "ollama"], ["offline", "openai"])
