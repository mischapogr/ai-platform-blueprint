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
    (STATE / "backups").mkdir(exist_ok=True, mode=0o700)
    environment = (STATE / "compose.env").read_text()
    if "LAB_DISTRIBUTION_IMAGE=" not in environment:
        environment += "LAB_DISTRIBUTION_IMAGE=" + images["distribution"]["reference"] + "\n"
    (STATE / "compose.env").write_text(environment)
    (STATE / "Caddyfile").write_text("""{
  admin off
  auto_https off
}
:5444 {
  tls /tls/server.crt /tls/server.key
  reverse_proxy nexus:8081
}
:5443 {
  tls /tls/server.crt /tls/server.key
  route {
    forward_auth alerts:9099 {
      uri /auth
    }
    handle /lab-alerts {
      reverse_proxy alerts:9099
    }
    handle /backup-store/* {
      reverse_proxy alerts:9099
    }
    handle {
      reverse_proxy distribution:5000
    }
  }
}
""")
    compose("up", "--detach", "distribution", "alerts", "registry")
    # A bind-mounted Caddyfile change does not alter the Compose service hash.
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
