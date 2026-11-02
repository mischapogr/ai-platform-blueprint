"""Guard scan failures and keep unfixed vulnerabilities blocking."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "findings,scanner_status,expected",
    [
        ([], 0, 0),
        (
            [
                {
                    "Severity": "HIGH",
                    "PkgName": "synthetic-lib",
                    "InstalledVersion": "1.0",
                    "VulnerabilityID": "CVE-TEST-0001",
                }
            ],
            1,
            1,
        ),
        ([], 7, 7),
    ],
)
def test_scan_wrapper_preserves_gate_and_operational_failures(
    tmp_path, findings, scanner_status, expected
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for filename in ["scan-image.sh", "summarize-image-scan.py"]:
        shutil.copy2(ROOT / "scripts" / filename, scripts / filename)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env bash
set -eu
if [[ "$1" == save ]]; then
  : > "$3"
  exit 0
fi
cp "$FAKE_REPORT" .local/evidence/trivy.json
exit "$FAKE_SCAN_STATUS"
""")
    docker.chmod(0o755)
    report = tmp_path / "source.json"
    report.write_text(
        json.dumps(
            {
                "SchemaVersion": 2,
                "Results": [{"Target": "synthetic-image", "Vulnerabilities": findings}],
            }
        )
    )
    summary = tmp_path / "github-summary.md"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "FAKE_REPORT": str(report),
        "FAKE_SCAN_STATUS": str(scanner_status),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", str(scripts / "scan-image.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603,S607
    assert result.returncode == expected
    assert "HIGH:" in result.stdout and "CRITICAL:" in result.stdout
    assert summary.exists()
    if scanner_status:
        assert f"Scanner exit: {scanner_status}" in summary.read_text()
    assert not (tmp_path / ".local/evidence/api-image.tar").exists()
    if findings:
        assert "CVE-TEST-0001" in result.stdout and "not available" in result.stdout


@pytest.mark.parametrize("contents", ["not json", "{}", '{"SchemaVersion":2,"Results":[]}'])
def test_invalid_or_empty_scan_never_passes(tmp_path, contents):
    evidence = tmp_path / ".local/evidence"
    evidence.mkdir(parents=True)
    report = evidence / "trivy.json"
    report.write_text(contents)
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts/summarize-image-scan.py"), str(report)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603
    assert result.returncode == 2
    assert "unavailable or invalid" in result.stderr
