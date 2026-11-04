# Local OCI registry, signed deployment and fresh-VM recovery

This profile keeps infrastructure on one workstation: a TLS/authenticated OCI
Distribution registry, a QEMU Ubuntu guest, Terraform's local state, Ansible,
Prometheus, node exporter, Alertmanager, Grafana, Loki, Alloy and Tempo. The owner selected the standard OCI
registry; Nexus is optional and its Community Edition terms were not accepted.
There is no public registry, cloud account, external signing identity or spending.

The guest runs the same UI/API/worker/PostgreSQL application with offline retrieval.
The separate [local model profile](LOCAL-MODELS.md) provides Ollama semantic
embeddings and generation on the workstation. GPU/model weights are not part of
this guest's signed release or its recovery qualification.

## Prerequisites and setup

Use Linux amd64 with Docker Compose, QEMU (`qemu-system-x86_64`, `qemu-img`),
`genisoimage`, OpenSSH, OpenSSL and Terraform 1.4–1.x. KVM is optional; the measured
run used KVM acceleration. Allow roughly 8 GiB available RAM and 25 GiB free disk
for this profile and cached images, in addition to any running model server.
Nothing installs packages or changes Docker trust/SSH configuration on the host.
The base VM image and pinned container/tool downloads require internet access.
The Ubuntu image is pinned to the [20261017 vendor build and checksum](https://cloud-images.ubuntu.com/noble/20261017/SHA256SUMS),
not the moving `current` URL.

```bash
uv sync --frozen
uv venv .local/infra-venv
uv pip install --python .local/infra-venv/bin/python 'ansible-core==2.20.2'
make release-package
make lab-up
make lab-publish
make lab-vm
make lab-provision
make lab-deploy
make lab-check
```

`lab-up` installs a checksum-verified project-local Cosign binary and generates a
project-specific CA, TLS certificate, encrypted signing key and registry password.
It verifies the release bundle before using its API image as a small lab utility
container. It uses pinned registry/tool images from `infra/local-lab/images.lock.json`.
There is no Docker socket in any lab container. Utility containers only mount their
specific input/output files. Registry storage and the alert receiver run outside
the application guest, but on the same physical machine.

Terraform uses its built-in `terraform_data` resource to create/delete a real QEMU
process and a fresh qcow2 overlay. This is a local reference module, not a cloud
provider implementation. State is under `.local/lab/terraform`, and all guest files
are under `.local/lab/vms/<name>`. SSH keys and known-host files belong only to the
lab; global SSH agents and configuration are excluded. Guest registry pulls use an
SSH reverse tunnel, so Docker only needs the lab CA inside this guest.

Ansible installs Docker in the guest, verifies the release signature, copies only
the generated configuration/secrets, pulls digest-pinned images and applies the
additive SQL migrations. A signature failure stops deployment before application
changes. The local public key in `.secrets/local-lab/cosign.pub` is the development
trust anchor; protect/pin its fingerprint independently when distributing releases.
The signed envelope binds the published image digests to the scanned bundle's
checksum/source-material identity. It is not an externally attested SLSA release.

## Open the UI

Read the chosen ports from `.local/lab/state.json`; preferred ports are:

- UI: `https://localhost:18443` (HTTP `18080` redirects to HTTPS).
- Grafana: `https://localhost:18443/grafana/` (separate local monitoring login).
- OCI registry: `https://localhost:15443` (requires authentication).
- Guest Prometheus: `http://localhost:19090` (local management only).
- Guest SSH: `127.0.0.1:16222`, using `.secrets/local-lab/lab_ssh`.

The browser will ask you to review the lab's private CA. Its certificate is
`.secrets/local-lab/ca.crt`; this workflow does not install it into host/browser
trust. Automated HTTPS checks explicitly trust that certificate. Unlock the UI
with the value in `.secrets/local-lab/workspace_token`. Credentials are never
included in public documentation, images or receipts.

Create a collection, add a text/file/public website source, wait for `ready`, then
ask a question in knowledge mode. Offline mode returns matching passages with
source references. Semantic embeddings and generated answers require the optional
model provider configuration described in `LOCAL-MODELS.md`.

Generic website ingestion accepts public HTTPS HTML and follows a bounded set of
same-host pages while respecting robots rules. It rejects loopback/private IPs,
including a WordPress site on this workstation. It does not execute JavaScript.
Public WordPress rendered pages can use this importer. A dedicated WordPress REST
connector for explicitly trusted private sites, authenticated drafts, pagination
and synchronization is still future work; do not weaken the generic SSRF boundary
to connect a private CMS. No existing CMS or credentials were accessed by this lab.

## Monitoring and troubleshooting

The VM now includes the monitoring services, API/worker trace export, proxy/API log
collection and a provisioned operator dashboard. Username `admin` and password file
`.secrets/local-lab/grafana_password` open Grafana. Follow the
[local observability walkthrough](LOCAL-OBSERVABILITY.md) for health, Loki queries,
trace links, diagnosis and an evidence-based view of remaining requirements.

```bash
make lab-diagnose LAB_VM=recovery
make lab-monitoring-test LAB_VM=recovery
```

The monitoring regression includes large synthetic PDFs through the actual TLS
proxy, preserved size limits, an intentional parser failure, independent traces
linked to the correct worker job, and log/trace privacy. It leaves the clearly
named synthetic collection for operator exploration. It does not use real sources.

## Acceptance and recovery

`make lab-check` uses synthetic content only. It verifies TLS/UI access, HTTP
redirects, protected workspace requests, origin rejection, denied legacy/management
paths, worker ingestion, chat retrieval, a bounded burst producing HTTP 429,
guest disk/memory metrics, and external firing/resolved database alerts. It stops
and restarts only the guest database and records `.local/lab/acceptance.json`.
The external receiver records only allowlisted alert names/status/timestamps.
Disk threshold configuration and actual metrics are present; filling the guest
disk and sustained load/capacity were not qualified.

```bash
make lab-recover
```

This destructive drill targets **only the owned disposable `primary` guest**. It
uploads an encrypted PostgreSQL dump to the authenticated TLS lab store, downloads
and authenticates it, rejects wrong-key/corrupt archives, and records hashes of
collections, source payloads, chunks and vectors. Only ciphertext is persisted.
The symmetric key lives separately in `.secrets/local-lab/escrow/backup.key`.
Escrow is independent of the guest and must be backed up separately for real use.

After backup verification, the drill writes an intentionally unrecoverable new
collection, destroys the primary guest/disk through Terraform, and provisions a
fresh `recovery` guest from the base image. Runtime passwords/access key are
regenerated. It restores with the escrow key, reapplies application grants,
compares the database snapshot, checks the expected loss of the later write, and
verifies both restored search and new ingestion. Failures retain diagnostics and
remaining owned state; do not delete state or retry destructive steps blindly.
The final `.local/lab/recovery-receipt.json` records measured RPO/RTO and limits.
The last three successfully indexed backup objects are retained; failed attempts
can leave diagnostic ciphertext. This drill is manual, not a scheduled backup
service or a production recovery policy.

After recovery, use `LAB_VM=recovery` for guest commands, for example:

```bash
make lab-check LAB_VM=recovery
make lab-backup LAB_VM=recovery
```

This proves recovery across independent local guest disks, not physical offsite
recovery, workstation-loss tolerance, production capacity or internet hardening.
Remote/cloud provisioning, offsite storage, scheduling/failure escalation,
independent key recovery custody and production RPO/RTO remain unqualified.

## Image updates and cleanup

Image locks record the observed upstream reference and immutable digest. Update
one dependency deliberately: pull the reviewed version, replace its lock entry
with the inspected repository/config digests, rerun the strict API release scan,
republish/sign and repeat deployment/recovery checks. Existing locks are not
silently refreshed to `latest`. Upstream runtime images beyond the API are pinned
and mirrored; their own SBOMs/vulnerability qualification are not implied by the
API scan. The lab certificate expires after 30 days; inspect/renew it deliberately.

Destroy only the selected guest through its existing Terraform state:

```bash
make lab-destroy LAB_VM=recovery
```

Stop the owned registry project with `make lab-stop`. The proxy metadata log-rotation
timer runs only inside the guest and disappears with its disposable disk. Volumes, encrypted backups,
keys, image cache and receipts are retained for review. No wildcard Docker cleanup
or deletion of another project's containers/volumes is performed. This profile
creates no recurring cloud costs; the operator owns local resource usage and key
custody. Never commit `.local`, `.secrets`, VM disks, private corpora or state.

### Extended monitoring, schedules and security qualification

The local profile now includes outside-guest verified HTTPS probes, aggregate
PostgreSQL/Redis exporters, guest-only container resource history and a **Local
alerts** Grafana/Alertmanager inbox. Follow [LOCAL-OBSERVABILITY.md](LOCAL-OBSERVABILITY.md)
for queries, bounded fault checks and schedule ownership.

`make lab-schedule-install LAB_VM=recovery` enables only this project's user timers:
workflow every 15 minutes, backup at 19:20, isolated restore at 19:40 (host local
time). They require the user manager and Docker; no linger/global settings are
changed. Disable those project timers before stopping/removing this lab. Backup
and comparison use one database snapshot and are encrypted. The isolated restore
uses a temporary, network-disabled database; it never replaces the working VM.

`make lab-runtime-audit` scans every runtime image and emits CycloneDX SBOMs.
`make lab-qualified-publish` requires a fresh full-runtime gate before any
publication and signs the summary checksum. Unknown or applicable HIGH/CRITICAL
findings block this path. The two exact signed Tempo plugin findings have a
[source applicability assessment](TEMPO-SOURCE-ASSESSMENT.md); raw findings stay visible. `lab-publish` is explicitly a development publication and records that full
runtime security is unqualified. Never treat the earlier clean API-only scan as
approval of every supporting service.
