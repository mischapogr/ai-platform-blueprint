"""TLS publication plus local-key release signatures, with no external signing services."""

import base64
import importlib.util
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from .bootstrap import cosign
from .common import BIN, PRIVATE, ROOT, STATE, docker, image_lock, run, save, secret, state


def bundle_module():
    spec = importlib.util.spec_from_file_location(
        "local_release", ROOT / "scripts/release-artifacts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skopeo(*args, mounts=None):
    image = image_lock()["skopeo"]["reference"]
    auth = PRIVATE / "registry-auth.json"
    cfg = state()
    value = base64.b64encode(("lab:" + secret("registry-password").read_text()).encode()).decode()
    save(auth, {"auths": {f"localhost:{cfg['registry_port']}": {"auth": value}}})
    command = [
        "run",
        "--rm",
        "--network",
        "host",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={auth},dst=/auth.json,readonly",
        "--mount",
        f"type=bind,src={PRIVATE / 'ca.crt'},dst=/certs/ca.crt,readonly",
    ]
    for source, target, readonly in mounts or []:
        command.extend(
            ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
        )
    return docker(*command, image, *args)


def copy_archive(archive, name):
    cfg = state()
    tag = "lab-" + uuid.uuid4().hex[:12]
    ref = f"localhost:{cfg['registry_port']}/{name}:{tag}"
    output = STATE / "transfers" / tag
    output.mkdir(parents=True, exist_ok=True)
    skopeo(
        "copy",
        "--authfile",
        "/auth.json",
        "--dest-cert-dir",
        "/certs",
        "--digestfile",
        "/out/digest",
        "docker-archive:/input/image.tar",
        "docker://" + ref,
        mounts=[(archive, "/input/image.tar", True), (output, "/out", False)],
    )
    digest = (output / "digest").read_text().strip()
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError("Registry did not return a SHA-256 manifest digest")
    immutable = ref.rsplit(":", 1)[0] + "@" + digest
    manifest = json.loads(
        skopeo(
            "inspect",
            "--raw",
            "--authfile",
            "/auth.json",
            "--cert-dir",
            "/certs",
            "docker://" + immutable,
        )
    )
    return {"reference": immutable, "config_digest": manifest["config"]["digest"]}


def publish(bundle=None):
    cfg = state()
    if not cfg.get("registry_ready"):
        raise RuntimeError("Initialize the local OCI registry first")
    release = bundle_module()
    if bundle is None:
        paths = list((ROOT / ".local/evidence/releases").glob("api-release-*"))
        bundle = max(paths, key=lambda p: p.stat().st_mtime)
    release.verify(bundle)
    provenance = json.loads((bundle / "provenance.json").read_text())
    previous = STATE / "published.json"
    published = json.loads(previous.read_text()) if previous.exists() else {"images": {}}
    images = published["images"]
    expected = provenance["image_config_digest"]
    if images.get("api", {}).get("config_digest") != expected:
        print("Publishing the verified API/worker archive to the local OCI registry.", flush=True)
        result = copy_archive(bundle / "image.tar", "ai-platform-blueprint/api")
        if result["config_digest"] != expected:
            raise ValueError("Published image config differs from the scanned archive")
        images["api"] = result
        save(previous, published)
    for name in ("db", "redis", "proxy", "prometheus", "alertmanager", "node"):
        locked = image_lock()[name]
        if images.get(name, {}).get("config_digest") == locked["config_digest"]:
            continue
        print("Mirroring digest-pinned runtime image: " + name, flush=True)
        with tempfile.TemporaryDirectory(prefix="mirror-", dir=STATE) as temp:
            archive = Path(temp) / "image.tar"
            docker("save", "--output", str(archive), locked["config_digest"])
            result = copy_archive(archive, "ai-platform-blueprint/" + name)
            if result["config_digest"] != locked["config_digest"]:
                raise ValueError("Registry copy changed a runtime image config")
            images[name] = result
            save(previous, published)
    published.update(
        {
            "schema": 1,
            "lab_id": cfg["id"],
            "api_bundle_manifest_sha256": release.digest(bundle / "manifest.json"),
            "api_source_materials_sha256": provenance["source_materials_sha256"],
            "scope": (
                "local development signing identity; no external registry or transparency service"
            ),
        }
    )
    save(previous, published)
    binary = cosign()
    env = {**os.environ, "COSIGN_PASSWORD": secret("signing-password").read_text()}
    signature = STATE / "published.sig"
    run(
        [
            str(binary),
            "sign-blob",
            "--key",
            str(PRIVATE / "cosign.key"),
            "--use-signing-config=false",
            "--tlog-upload=false",
            "--new-bundle-format=false",
            "--output-signature",
            str(signature),
            str(previous),
        ],
        env=env,
    )
    verify(previous, signature)
    rejected = False
    # Negative proof: an altered signed payload and an unrelated key must be refused.
    with tempfile.TemporaryDirectory(prefix="signature-negative-", dir=STATE) as temp:
        temp = Path(temp)
        altered = temp / "altered.json"
        altered.write_bytes(previous.read_bytes() + b" ")
        try:
            verify(altered, signature)
        except subprocess.CalledProcessError:
            rejected = True
        else:
            raise AssertionError("Tampered release manifest passed signature verification")
        run([str(binary), "generate-key-pair", "--output-key-prefix", str(temp / "wrong")], env=env)
        try:
            verify(previous, signature, key=temp / "wrong.pub")
        except subprocess.CalledProcessError:
            rejected = True
        else:
            raise AssertionError("Untrusted signing key passed verification")
    receipt = {
        "local_registry": f"localhost:{cfg['registry_port']}",
        "signed_manifest": str(previous.relative_to(ROOT)),
        "public_key_sha256": release.digest(PRIVATE / "cosign.pub"),
        "signature_verified": True,
        "tamper_rejected": rejected,
        "wrong_key_rejected": True,
        "external_signing_services_used": False,
    }
    save(STATE / "signing-receipt.json", receipt)
    print("Local release signature and negative verification checks passed.", flush=True)
    return published


def verify(manifest=None, signature=None, *, key=None):
    manifest = manifest or STATE / "published.json"
    signature = signature or STATE / "published.sig"
    key = key or PRIVATE / "cosign.pub"
    run(
        [
            str(BIN / "cosign"),
            "verify-blob",
            "--key",
            str(key),
            "--signature",
            str(signature),
            "--offline=true",
            "--insecure-ignore-tlog=true",
            str(manifest),
        ]
    )
    data = json.loads(manifest.read_text())
    if data.get("lab_id") != state()["id"]:
        raise ValueError("Release belongs to a different local signing scope")
    prefix = f"localhost:{state()['registry_port']}/ai-platform-blueprint/"
    if any(
        not item["reference"].startswith(prefix) or "@sha256:" not in item["reference"]
        for item in data["images"].values()
    ):
        raise ValueError("Release contains an unapproved registry reference")
    return data
