"""Strict vulnerability and CycloneDX inventory for all pinned local runtime images."""

import json
import os
import tempfile
from datetime import UTC, datetime

from .common import DOCKER, ROOT, STATE, compose, docker, image_lock, run, save
from .vulnerability_assessment import assess_image, validate_result


def runtime_inventory(lock=None, *, api_digest=None):
    full_inventory = lock is None
    lock = image_lock() if full_inventory else lock
    published = STATE / "published.json"
    inventory = {
        name: value["config_digest"]
        for name, value in lock.items()
        if name not in {"nexus", "skopeo"}
    }
    if api_digest:
        inventory["api"] = api_digest
    elif published.exists():
        inventory["api"] = json.loads(published.read_text())["images"]["api"]["config_digest"]
    if full_inventory:
        controller = json.loads(compose("config", "--format", "json"))["services"]
        for name, service in controller.items():
            inventory["controller_" + name] = json.loads(
                docker("image", "inspect", service["image"])
            )[0]["Id"]

    return inventory


def require_clean_receipt(receipt, expected):
    rows = receipt.get("images", {})
    if (
        receipt.get("full_inventory") is not True
        or set(rows) != set(expected)
        or receipt.get("expected_images") != sorted(expected)
    ):
        raise ValueError("Runtime scan inventory incomplete")
    for name, image in expected.items():
        row = rows[name]
        if row.get("image_config_digest") != image or row.get("passed") is not True:
            raise ValueError("Runtime scan does not approve the exact release inventory")
        validate_result(row, image)


def audit(lock=None, *, api_digest=None):
    full_inventory = lock is None
    inventory = runtime_inventory(lock, api_digest=api_digest)
    report_dir = ROOT / (
        ".local/evidence/runtime-security"
        if full_inventory
        else ".local/evidence/runtime-security-candidates"
    )
    report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = ROOT / ".local/trivy-user-cache"
    cache.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, image in inventory.items():
        directory = report_dir / name
        directory.mkdir(exist_ok=True, mode=0o700)
        try:
            with tempfile.TemporaryDirectory(prefix="runtime-scan-", dir=STATE) as temp:
                from pathlib import Path

                archive = Path(temp) / "image.tar"
                docker("save", "--output", str(archive), image)
                for kind, fmt in (("vulnerabilities", "json"), ("sbom", "cyclonedx")):
                    output = directory / (kind + ".json")
                    output.write_text("")
                    output.chmod(0o600)
                    args = [
                        DOCKER,
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
                        f"type=bind,src={archive},dst=/input/image.tar,readonly",
                        "--mount",
                        f"type=bind,src={output},dst=/output/report.json",
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
                        fmt,
                        "--output",
                        "/output/report.json",
                    ]
                    if kind == "vulnerabilities":
                        args += ["--severity", "HIGH,CRITICAL", "--exit-code", "1"]
                    result = run(args, check=False, timeout=600)
                    if result.returncode not in (0, 1) or not output.stat().st_size:
                        raise RuntimeError("Scanner failed")
                    parsed = json.loads(output.read_text())
                    if kind == "vulnerabilities":
                        if (
                            parsed.get("Metadata", {}).get("ImageID") != image
                            or parsed.get("SchemaVersion") != 2
                            or not isinstance(parsed.get("Results"), list)
                        ):
                            raise RuntimeError("Malformed scanner report")
                        found = any(row.get("Vulnerabilities") for row in parsed["Results"])
                        if result.returncode != int(found):
                            raise RuntimeError("Scanner status/report disagree")
                    if kind == "sbom" and (
                        result.returncode or parsed.get("bomFormat") != "CycloneDX"
                    ):
                        raise RuntimeError("SBOM generation failed")
            raw = json.loads((directory / "vulnerabilities.json").read_text())
            vulnerabilities = [
                {**v, "Target": row["Target"]}
                for row in raw.get("Results", [])
                for v in row.get("Vulnerabilities", [])
            ]
            counts = {
                severity: sum(v["Severity"] == severity for v in vulnerabilities)
                for severity in ("HIGH", "CRITICAL")
            }
            assessed, artifact_sha, vex_sha = assess_image(name, image, vulnerabilities, directory)
            unresolved = {"HIGH": counts["HIGH"] - len(assessed), "CRITICAL": counts["CRITICAL"]}
            results[name] = {
                "image_config_digest": image,
                **counts,
                "passed": not any(unresolved.values()),
                "unresolved_HIGH": unresolved["HIGH"],
                "unresolved_CRITICAL": unresolved["CRITICAL"],
                "assessed_not_affected": assessed,
                "artifact_binary_sha256": artifact_sha,
                "assessment_vex_sha256": vex_sha,
                "findings": [
                    {
                        k: v.get(k)
                        for k in (
                            "VulnerabilityID",
                            "PkgName",
                            "FixedVersion",
                            "InstalledVersion",
                            "Severity",
                            "Target",
                        )
                    }
                    for v in vulnerabilities
                ],
            }
            print(
                name
                + ": raw="
                + str(counts)
                + "; verified not affected="
                + str(len(assessed))
                + "; unresolved="
                + str(unresolved),
                flush=True,
            )
        except Exception as error:
            results[name] = {
                "image_config_digest": image,
                "passed": False,
                "error_class": type(error).__name__,
            }
            print(name + ": scanner error (kept blocking)", flush=True)
        save(
            report_dir / "summary.json",
            {
                "time_utc": datetime.now(UTC).isoformat(),
                "full_inventory": full_inventory,
                "expected_images": sorted(inventory),
                "images": results,
            },
        )
    blocked = [name for name, result in results.items() if not result["passed"]]
    print(
        ("Runtime security gate: " if full_inventory else "Candidate subset scan: ")
        + ("PASS" if not blocked else "BLOCKED: " + ", ".join(blocked))
    )
    return not blocked
