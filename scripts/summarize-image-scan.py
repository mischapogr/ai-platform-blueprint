#!/usr/bin/env python3
"""Render the strict HIGH/CRITICAL policy report for local logs and GitHub summaries."""

import json
import os
import sys
from pathlib import Path


def summarize(report):
    if (
        not isinstance(report, dict)
        or report.get("SchemaVersion") != 2
        or not isinstance(report.get("Results"), list)
        or not report["Results"]
    ):
        raise ValueError("Missing or unsupported scan results")
    findings = []
    for result in report["Results"]:
        if not isinstance(result, dict) or not result.get("Target"):
            raise ValueError("Missing scan target")
        for item in result.get("Vulnerabilities") or []:
            if item["Severity"] not in {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                raise ValueError("Unexpected vulnerability severity")
            if item["Severity"] in {"HIGH", "CRITICAL"}:
                findings.append(item)
    counts = {
        level: sum(v["Severity"] == level for v in findings) for level in ["HIGH", "CRITICAL"]
    }
    text = (
        "## API image vulnerability scan\n\n"
        f"HIGH: **{counts['HIGH']}**, CRITICAL: **{counts['CRITICAL']}**. "
        "Unfixed findings remain blocking.\n\n"
    )
    if findings:
        text += "| Severity | Package | Installed | Fixed | Advisory |\n|---|---|---|---|---|\n"
        for item in findings:
            values = [
                item["Severity"],
                item["PkgName"],
                item["InstalledVersion"],
                item.get("FixedVersion") or "not available",
                item["VulnerabilityID"],
            ]
            text += (
                "| "
                + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values)
                + " |\n"
            )
    else:
        text += "No HIGH/CRITICAL vulnerabilities found by this scan.\n"
    return text, bool(findings)


def main():
    try:
        report = json.loads(Path(sys.argv[1]).read_text())
        text, blocked = summarize(report)
        scanner_status = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        if scanner_status:
            text += f"\n**Scanner exit: {scanner_status}. This scan did not pass.**\n"
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f"Image scan report unavailable or invalid: {type(error).__name__}", file=sys.stderr)
        return 2
    print(text, end="")
    Path(".local/evidence/trivy-summary.md").write_text(text)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as stream:
            stream.write(text + "\n")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
