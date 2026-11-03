"""Disposable QEMU guests; no libvirt/global configuration or host disk passthrough."""

import json
import os
import re
import shutil
import signal
import socket
import time
from pathlib import Path

from .common import PRIVATE, STATE, download, free_port, run, save, state, wait

IMAGE_SHA = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"
IMAGE_URL = "https://cloud-images.ubuntu.com/noble/20261017/noble-server-cloudimg-amd64.img"


def directory(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
        raise ValueError("Invalid local VM name")
    return STATE / "vms" / name


def qmp(name, operation):
    folder = directory(name)
    marker = json.loads((folder / "metadata.json").read_text())
    if marker["lab_id"] != state()["id"]:
        raise ValueError("VM does not belong to this lab")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(folder / "monitor.sock"))
        stream = client.makefile("rwb")
        json.loads(stream.readline())
        for command in ("qmp_capabilities", operation):
            stream.write((json.dumps({"execute": command}) + "\n").encode())
            stream.flush()
            while True:
                value = json.loads(stream.readline())
                if "return" in value or "error" in value:
                    break
        return value


def ssh(name, command, *, input=None, timeout=600, check=True):
    folder = directory(name)
    marker = json.loads((folder / "metadata.json").read_text())
    if marker["lab_id"] != state()["id"]:
        raise ValueError("Unknown local VM ownership")
    args = [
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
        "-i",
        str(PRIVATE / "lab_ssh"),
        "-p",
        str(marker["ssh_port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile=" + str(folder / "known_hosts"),
        "lab@127.0.0.1",
        command,
    ]
    return run(args, input=input, timeout=timeout, check=check)


def create(name):
    cfg = state()
    if "metrics_port" not in cfg:
        cfg["metrics_port"] = free_port(19090)
        save(STATE / "state.json", cfg)
    folder = directory(name)
    marker_path = folder / "metadata.json"
    if marker_path.exists():
        marker = json.loads(marker_path.read_text())
        if marker["lab_id"] != cfg["id"]:
            raise ValueError("Refusing to overwrite another VM")
        try:
            if qmp(name, "query-status").get("return", {}).get("running"):
                print("Owned local VM is already running: " + name, flush=True)
                return marker
        except OSError:
            pass
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = PRIVATE / "lab_ssh"
    if not key.exists():
        PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        run(["/usr/bin/ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)])
        key.chmod(0o600)
    image = STATE / "base/ubuntu.qcow2"
    print("Verifying the local guest base image.", flush=True)
    download(IMAGE_URL, image, IMAGE_SHA)
    disk = folder / "disk.qcow2"
    if disk.is_symlink():
        raise ValueError("Refusing a symlinked VM disk")
    if not disk.exists():
        (folder / "known_hosts").unlink(missing_ok=True)
        run(
            [
                "/usr/bin/qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(image),
                str(disk),
                "24G",
            ]
        )
        disk.chmod(0o600)
    userdata = {
        "users": [
            {
                "name": "lab",
                "groups": ["sudo"],
                "shell": "/bin/bash",
                "lock_passwd": True,
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "ssh_authorized_keys": [(PRIVATE / "lab_ssh.pub").read_text().strip()],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "package_update": False,
    }
    (folder / "user-data").write_text("#cloud-config\n" + json.dumps(userdata))
    (folder / "meta-data").write_text(
        json.dumps({"instance-id": cfg["id"] + "-" + name, "local-hostname": "blueprint-" + name})
    )
    run(
        [
            "/usr/bin/genisoimage",
            "-output",
            str(folder / "seed.iso"),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(folder / "user-data"),
            str(folder / "meta-data"),
        ]
    )
    marker = {
        "lab_id": cfg["id"],
        "name": name,
        "image_sha256": IMAGE_SHA,
        "ssh_port": cfg["ssh_port"],
        "http_port": cfg["http_port"],
        "https_port": cfg["https_port"],
        "metrics_port": cfg.get("metrics_port", 19090),
    }
    save(marker_path, marker)
    monitor = folder / "monitor.sock"
    if monitor.exists():
        monitor.unlink()
    network = (
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{marker['ssh_port']}-:22,"
        f"hostfwd=tcp:127.0.0.1:{marker['http_port']}-:8080,"
        f"hostfwd=tcp:127.0.0.1:{marker['https_port']}-:8443,"
        f"hostfwd=tcp:127.0.0.1:{marker['metrics_port']}-:9090"
    )
    acceleration = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg,thread=multi"
    args = [
        shutil.which("qemu-system-x86_64") or "/usr/bin/qemu-system-x86_64",
        "-accel",
        acceleration,
        "-cpu",
        "host" if acceleration == "kvm" else "max",
        "-smp",
        "2",
        "-m",
        "4096",
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-cdrom",
        str(folder / "seed.iso"),
        "-netdev",
        network,
        "-device",
        "virtio-net-pci,netdev=net0",
        "-display",
        "none",
        "-serial",
        "file:" + str(folder / "serial.log"),
        "-monitor",
        "none",
        "-qmp",
        "unix:" + str(monitor) + ",server=on,wait=off",
        "-daemonize",
        "-pidfile",
        str(folder / "qemu.pid"),
    ]
    run(args)
    marker["acceleration"] = acceleration
    save(marker_path, marker)
    print("Waiting for isolated guest SSH/cloud-init: " + name, flush=True)
    wait(
        lambda: ssh(name, "true", timeout=8).returncode == 0,
        seconds=600,
        message="Guest SSH did not become ready",
    )
    ssh(name, "timeout 600 cloud-init status --wait", timeout=650)
    marker["boot_ready"] = True
    save(marker_path, marker)
    return marker


def destroy(name):
    folder = directory(name)
    marker = json.loads((folder / "metadata.json").read_text())
    if marker["lab_id"] != state()["id"]:
        raise ValueError("Refusing to destroy an unowned guest")
    try:
        qmp(name, "quit")
    except (OSError, ValueError):
        pass
    pidfile = folder / "qemu.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text())
        proc = Path(f"/proc/{pid}/cmdline")
        deadline = time.monotonic() + 10
        while proc.exists() and time.monotonic() < deadline:
            command = proc.read_bytes().decode(errors="replace")
            if not command:
                break
            if (
                str(folder / "disk.qcow2") not in command
                or str(folder / "monitor.sock") not in command
            ):
                raise ValueError("VM PID ownership changed; refusing to signal it")
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.2)
        if proc.exists() and proc.read_bytes():
            raise RuntimeError("Owned VM did not stop; disk retained")
    for filename in (
        "disk.qcow2",
        "seed.iso",
        "user-data",
        "meta-data",
        "monitor.sock",
        "qemu.pid",
    ):
        path = folder / filename
        if path.is_symlink():
            raise ValueError("Refusing to remove a VM symlink")
        path.unlink(missing_ok=True)
    marker["destroyed"] = True
    save(folder / "metadata.json", marker)
    print("Destroyed only the owned disposable guest disk/process: " + name)
