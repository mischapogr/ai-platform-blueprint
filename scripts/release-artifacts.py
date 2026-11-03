#!/usr/bin/env python3
"""Build and verify an unsigned local API/worker release evidence bundle. Never pushes."""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER = shutil.which("docker") or "/usr/bin/docker"
FILES = {"image.tar", "sbom.json", "vulnerabilities.json", "provenance.json", "LICENSE", "NOTICE"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def materials():
    paths = [
        ROOT / p
        for p in (
            "apps/api/Dockerfile",
            ".dockerignore",
            "requirements.txt",
            "uv.lock",
            "LICENSE",
            "NOTICE",
        )
    ]
    for directory, pattern in (
        ("apps/api/platform_api", "*.py"),
        ("apps/worker", "*.py"),
        ("apps/demo-rag/frontend", "*"),
        ("docs/runbooks", "*.md"),
    ):
        paths.extend(p for p in (ROOT / directory).glob(pattern) if p.is_file())
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def run(*args, timeout=600):
    return subprocess.run(  # noqa: S603 - explicit argv, no shell
        [DOCKER, *args],
        check=True,
        cwd=ROOT,
        text=True,  # noqa: S603
        capture_output=True,
        timeout=timeout,
    ).stdout.strip()


def verify(directory):
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest must be a regular file")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("version") != 1 or set(manifest.get("sha256", {})) != FILES:
        raise ValueError("Unsupported bundle manifest")
    for name, expected in manifest["sha256"].items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError("Bundle integrity failed: " + name)
    return manifest


def capabilities(image):
    code = (
        "import json; from typing import get_args; "
        "from platform_api.knowledge import CollectionInput; "
        "print(json.dumps(list(get_args(CollectionInput.model_fields['embedding_provider'].annotation))))"
    )
    return json.loads(
        run(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            image,
            "python",
            "-c",
            code,
        )
    )


def ensure_compatible(used_providers, supported_providers):
    missing = sorted(set(used_providers) - set(supported_providers))
    if missing:
        raise ValueError("Target cannot read persisted embedding providers: " + ", ".join(missing))


def build():
    run_id = "api-release-" + uuid.uuid4().hex[:12]
    directory = ROOT / ".local/evidence/releases" / run_id
    directory.mkdir(parents=True, mode=0o700)
    before = materials()
    tag = "ai-platform-blueprint-release:" + run_id
    run("build", "--tag", tag, "--file", "apps/api/Dockerfile", ".")
    info = json.loads(run("image", "inspect", tag))[0]
    image = info["Id"]
    run("save", "--output", str(directory / "image.tar"), image)
    cache = ROOT / ".local/trivy-user-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for filename, output_format in (("vulnerabilities.json", "json"), ("sbom.json", "cyclonedx")):
        target = directory / filename
        target.touch(mode=0o600)
        args = [
            "run",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "TRIVY_CACHE_DIR=/cache",
            "--mount",
            f"type=bind,src={directory / 'image.tar'},dst=/input/image.tar,readonly",
            "--mount",
            f"type=bind,src={target},dst=/output/report.json",
            "--mount",
            f"type=bind,src={cache},dst=/cache",
            "aquasec/trivy:0.76.1",
            "image",
            "--input",
            "/input/image.tar",
            "--scanners",
            "vuln",
            "--ignorefile",
            "",
            "--format",
            output_format,
            "--output",
            "/output/report.json",
        ]
        if filename == "vulnerabilities.json":
            args.extend(["--severity", "HIGH,CRITICAL", "--exit-code", "1"])
        run(*args)
    spec = importlib.util.spec_from_file_location(
        "scan_summary", ROOT / "scripts/summarize-image-scan.py"
    )
    summary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summary)
    text, blocked = summary.summarize(json.loads((directory / "vulnerabilities.json").read_text()))
    if blocked or json.loads((directory / "sbom.json").read_text()).get("bomFormat") != "CycloneDX":
        raise ValueError("Release vulnerability/SBOM gate failed")
    if materials() != before:
        raise ValueError("Build inputs changed during packaging")
    git = shutil.which("git") or "/usr/bin/git"
    revision = subprocess.run(  # noqa: S603 - fixed Git arguments
        [git, "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,  # noqa: S603
        capture_output=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(  # noqa: S603 - fixed Git arguments
            [git, "status", "--porcelain"],
            cwd=ROOT,
            text=True,  # noqa: S603
            capture_output=True,
            check=True,
        ).stdout
    )
    provenance = {
        "schema": "local-api-build-v1",
        "built_at_utc": datetime.now(UTC).isoformat(),
        "builder": "local Docker; unsigned, no SLSA level claimed",
        "parent_revision": revision,
        "working_tree_dirty": dirty,
        "source_materials_sha256": before,
        "image_config_digest": image,
        "platform": {"os": info["Os"], "arch": info["Architecture"]},
        "embedding_providers": capabilities(image),
        "registry_manifest_digest": None,
        "scope": "API/worker only; optional model runtime and infrastructure excluded",
    }
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, directory / name)
    manifest = {
        "version": 1,
        "sha256": {name: digest(directory / name) for name in sorted(FILES)},
        "authenticated": False,
        "signing": "pending owner identity; no publication performed",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify(directory)
    print(text.strip())
    print("Verified unsigned local bundle: " + str(directory.relative_to(ROOT)))
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
        print("Bundle checksums verified; authenticity requires an owner signature.")
    else:
        build()
