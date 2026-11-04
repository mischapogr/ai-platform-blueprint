"""Guest-only Docker/cgroup sampler. No socket or host mounts in app containers."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

GUEST = Path("/opt/ai-platform-blueprint")
DOCKER = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]


def execute(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout  # noqa: S603


def number(path, default=0):
    try:
        value = path.read_text().strip()
        return int(value) if value.isdigit() else default
    except OSError:
        return default


def metrics(rows, stats, cgroups):
    output = []
    for row in rows:
        service = row["Service"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", service):
            continue
        cid = row["ID"]
        labels = "{service=" + json.dumps(service) + "}"
        output.append(
            "blueprint_container_running" + labels + " " + str(int(row["State"] == "running"))
        )
        if row.get("Health"):
            output.append(
                "blueprint_container_health" + labels + " " + str(int(row["Health"] == "healthy"))
            )
        stat = stats.get(cid, {})
        cpu = str(stat.get("CPUPerc", "0%")).removesuffix("%")
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cpu):
            output.append("blueprint_container_cpu_percent" + labels + " " + cpu)
        group = cgroups / ("docker-" + cid + ".scope")
        if row["State"] == "running" and not (group / "memory.current").is_file():
            raise RuntimeError("Running service cgroup unavailable; retaining previous sample")
        output.append(
            "blueprint_container_memory_bytes"
            + labels
            + " "
            + str(number(group / "memory.current"))
        )
        output.append(
            "blueprint_container_memory_limit_bytes"
            + labels
            + " "
            + str(number(group / "memory.max"))
        )
        try:
            cpu_stat = dict(line.split() for line in (group / "cpu.stat").read_text().splitlines())
            for key, metric in [
                ("nr_throttled", "blueprint_container_cpu_throttled_periods_total"),
                ("throttled_usec", "blueprint_container_cpu_throttled_microseconds_total"),
            ]:
                output.append(metric + labels + " " + str(int(cpu_stat.get(key, 0))))
        except (OSError, ValueError):
            pass
        try:
            events = dict(
                line.split() for line in (group / "memory.events").read_text().splitlines()
            )
            output.append(
                "blueprint_container_oom_kills_total"
                + labels
                + " "
                + str(int(events.get("oom_kill", 0)))
            )
        except (OSError, ValueError):
            pass
    output.append("blueprint_resource_sampler_last_success_unixtime " + str(time.time()))
    return "\n".join(output) + "\n"


def main():
    allowed = set(json.loads((GUEST / "compose.yaml").read_text())["services"])
    raw = execute(
        DOCKER
        + [
            "compose",
            "--project-name",
            "blueprint-local",
            "--file",
            str(GUEST / "compose.yaml"),
            "ps",
            "--all",
            "--format",
            "json",
        ]
    )
    rows = [json.loads(line) for line in raw.splitlines()]
    rows = [
        row
        for row in rows
        if row.get("Service") in allowed and re.fullmatch(r"[a-f0-9]{12,64}", row.get("ID", ""))
    ]
    # Full IDs are needed for cgroup paths; inspect returns no environment or payloads.
    ids = execute(
        DOCKER
        + [
            "inspect",
            "--format",
            '{{.Id}}|{{index .Config.Labels "com.docker.compose.service"}}',
            *[row["ID"] for row in rows],
        ]
    )
    full_ids = {line.split("|")[1]: line.split("|")[0] for line in ids.splitlines()}
    for row in rows:
        row["ID"] = full_ids[row["Service"]]
    raw = execute(DOCKER + ["stats", "--no-stream", "--format", "{{json .}}", *full_ids.values()])
    stats = {row["ID"]: row for row in (json.loads(line) for line in raw.splitlines())}
    # Docker stats IDs can be abbreviated even when full IDs were supplied.
    for row in rows:
        for key in list(stats):
            if row["ID"].startswith(key):
                stats[row["ID"]] = stats[key]
    text = metrics(rows, stats, Path("/sys/fs/cgroup/system.slice"))
    target = GUEST / "node-textfile/resources.prom"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.chmod(0o644)
    os.replace(temporary, target)
    print("Sampled project services:", len(rows))


if __name__ == "__main__":
    main()
