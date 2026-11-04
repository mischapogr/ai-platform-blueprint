"""EULA-independent local OCI registry while keeping Nexus available for review."""

import json
import ssl

import httpx

from .common import PRIVATE, ROOT, STATE, compose, docker, save, secret, state, wait


def init():
    cfg = state()
    lock = ROOT / "infra/local-lab/images.lock.json"
    images = json.loads(lock.read_text())
    if "distribution" not in images:
        info = json.loads(docker("image", "inspect", "registry:3"))[0]
        images["distribution"] = {
            "source": "registry:3",
            "reference": info["RepoDigests"][0],
            "config_digest": info["Id"],
        }
        save(lock, images, 0o644)
    secret("registry-password")
    (STATE / "registry-data").mkdir(exist_ok=True, mode=0o700)
    (STATE / "registry-config.yaml").write_text(
        (
            "version: 0.1\nstorage:\n  filesystem:\n"
            "    rootdirectory: /var/lib/registry\nhttp:\n  addr: :5000\n"
        )
    )
    (STATE / "automation").mkdir(exist_ok=True, mode=0o700)
    (STATE / "backups").mkdir(exist_ok=True, mode=0o700)
    from .controller import prepare

    prepare(images)
    compose("up", "--detach", "distribution", "alerts", "registry")
    # Reload the private file-provider configuration explicitly.
    compose("restart", "registry")
    context = ssl.create_default_context(cafile=str(PRIVATE / "ca.crt"))
    with httpx.Client(
        verify=context, trust_env=False, auth=("lab", secret("registry-password").read_text())
    ) as client:
        wait(lambda: client.get(f"https://localhost:{cfg['registry_port']}/v2/").status_code == 200)
    cfg.update(registry_ready=True, registry_backend="distribution", nexus_eula_pending=True)
    save(STATE / "state.json", cfg)
    print(
        "Standard OCI registry authenticated over verified local TLS; Nexus remains EULA-pending."
    )
