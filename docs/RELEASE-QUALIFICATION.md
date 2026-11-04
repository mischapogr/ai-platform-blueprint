# Local release artifacts and rollback qualification

B08 has a local build/evidence path and an optional owner-selected
[OCI registry and signed guest deployment](LOCAL-LAB.md). The packaging command
itself is offline publication-wise: it builds/scans without pushing or signing.
No Git state is modified.

```bash
make release-package
make rollback-test
```

## Artifact bundle

The packaging command builds a dedicated API/worker image, exports it by immutable
local image-config digest, scans the archive with the existing strict HIGH/CRITICAL
gate, and generates a CycloneDX SBOM. Trivy receives only the image archive, output
file and vulnerability cache; it never receives the Docker socket or workspace
secrets. Scanner failures and unfixed HIGH/CRITICAL findings remain blocking.

Each `.local/evidence/releases/api-release-*` bundle contains:

- `image.tar`: the exported API/worker image; runtime inference/model weights and
  infrastructure are outside this bundle's scope.
- `sbom.json` and `vulnerabilities.json`: detected packages and the blocking scan.
- `provenance.json`: parent Git revision, working-tree dirty flag, hashes of build
  materials, platform, image-config digest and introspected embedding capabilities.
- `LICENSE` and `NOTICE`: repository attribution, also included inside the image.
- `manifest.json`: SHA-256 checksums for the bundle files and explicit unsigned status.

Build input hashes are checked again after packaging. Bytecode caches are excluded
from the context, and API/worker copies select Python source rather than local
compiled files. Real sources, model weights, credentials and private runtime state
remain outside the image context. A dirty checkout is identified honestly; its
parent revision alone is not presented as the complete source identity.

Verify accidental corruption before inspecting/loading an archive:

```bash
.venv/bin/python scripts/release-artifacts.py --verify .local/evidence/releases/api-release-REPLACE_ME
```

Checksum verification is not authenticity verification: an attacker could replace
the files and the unsigned manifest together. The provenance is a local builder
record, not a signed SLSA attestation or a claimed SLSA level. No registry manifest
digest exists until publication; the local image-config digest is recorded
separately to avoid conflating these identities.

## Populated-data upgrade and rollback

The rollback test builds a reference image from local Git revision
`574e97a3bf816ab46bfdf0149315e185d8cfdae3` and a candidate from current source. The
reference source is extracted into a temporary directory; the checkout/index are
not changed. The reference commit must be available locally. To test another
reviewed reference explicitly:

```bash
.venv/bin/python scripts/qualify-rollback.py --reference COMMIT
```

The scenario creates its own internal Docker network and temporary database with
no published ports or workspace credentials. It seeds an offline collection using
the reference schema/image, applies current additive migrations, verifies that the
candidate can read the unchanged index, writes another source with the candidate,
and verifies both sources after returning to the reference image. Index content
and vectors are compared across transitions. Owned containers/networks are removed;
images/build cache and the ignored receipt remain for inspection.

A second scenario adds synthetic local-provider metadata. The reference image does
not support `ollama`, so capability checking refuses the rollback before running
reference application work against that data. This is significant: older adapters
could otherwise treat an unknown embedding provider as the external provider.
An empty unsupported collection is enough to block, conservatively.

The gate is exercised by the qualification tool. It is not wired into a remote
production deployment, and it is necessary rather than sufficient for arbitrary
schema/model rollback. The scenario uses direct application calls, not a running
HTTP proxy, browser sessions, TLS or production traffic. No zero-downtime promise
is made. An incompatible rollback needs a separately reviewed data restore or
forward repair; do not delete a live collection to make this test's gate pass.

## Local publication and remaining scope

The local lab mirrors the verified API archive and pinned runtime images into its
TLS/authenticated OCI registry. Skopeo compares the published OCI manifest's image
config digest with the scanned archive identity. A Cosign local encrypted key signs
the release envelope containing immutable image references and the API bundle/source
hashes. Deployment verifies this signature before applying configuration. Tampered
payload and unrelated-key verification are exercised and must fail. There are no
Fulcio/Rekor/OIDC calls, external signing identity or public publication.

See [LOCAL-LAB.md](LOCAL-LAB.md) for reproduction, private receipt paths and key
custody. Runtime dependency images are pinned and mirrored, but only the API image
has the described strict scan/SBOM qualification. This is a local development
identity, not a public release trust policy or independently attested provenance.
Cloud delivery and physical offsite recovery remain separate unqualified scopes.

## Full-runtime development and security gate

The lab now scans the complete pinned application/monitoring/controller inventory,
including the optional CPU model image, with CycloneDX SBOMs and strict HIGH/
CRITICAL failures. `make lab-qualified-publish` runs this gate before publication
and requires an exact, complete inventory with no unresolved HIGH/CRITICAL findings. It binds the summary hash
into the signed envelope. An ordinary development publication explicitly records
`full_runtime_security_passed=false`; it cannot inherit a prior qualification.

Runtime source recipes pin upstream archives, checksums and build arguments. The
registry and node exporter are rebuilt with patched Go dependencies; PostgreSQL
uses Alpine plus source-built pgvector and a patched gosu helper; Tempo stays on
a maintained 2.x release. The local registry TLS/auth proxy reuses pinned Traefik.
Grafana uses its official core image and checksum-pinned **official signed**
Prometheus/Loki/Tempo packages. Unused backend datasource packages are removed;
plugin signature validation is not disabled. Two raw Tempo-module findings in the
required signed Grafana plugin are handled by an
[exact-artifact source assessment](TEMPO-SOURCE-ASSESSMENT.md). Both affected
server packages are absent from the plugin and both upstream fixes are present
in its dependency revision. Unknown/changed findings remain blocking. No ignore
rule or unfixed-finding exemption was added. Refer to the current private scan rather than treating
an API-only clean scan as approval of supporting components.

The database-base migration uses a quiesced logical dump/restore into a new owned
volume, validates knowledge hashes/vectors before exposing the app, and retains
the original volume. Raw PostgreSQL files are not reused across libc environments.
Grafana's SQLite state is backed up consistently and encrypted before its major
version update. These measures do not establish public signing identity, offsite
recovery or zero-downtime deployment.
