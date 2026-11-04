# Exact-artifact Tempo vulnerability assessment

The local runtime gate retains two raw HIGH findings for the Tempo dependency
inside Grafana's official signed Tempo datasource 13.4.2. A local consumer
assessment classifies these exact findings as `not_affected` with the OpenVEX
justification `vulnerable_code_not_present`. This is a source applicability
assessment, not an upstream VEX statement or an acceptance of vulnerable code.

## Evidence and scope

The reviewed [evidence profile](../infra/local-lab/security/tempo-assessment.json)
binds the image-config digest, plugin binary SHA-256, dependency path/version,
scanner target path and the two CVE identifiers. Its own checksum is pinned in
[the evaluator](../scripts/local_lab/vulnerability_assessment.py).

The binary SHA-256 is
`74b1b188d86aa0f7a41cf0bf9f67f6b8a0a256acd811fddb89b5a91eec175895`.
It matches the binary from the checksum-verified official plugin archive and the
running guest. Grafana reported its plugin signature as valid, type `grafana`,
organization `Grafana Labs`. Signature enforcement remains enabled.

The binary's Go build information identifies Tempo module
`v1.5.1-0.20260910130453-bcfe9f230c1d`. The module checksum matches the downloaded
Go module. Its revision contains both upstream fixes:

- [CVE-2026-21728 advisory](https://grafana.com/security/security-advisories/cve-2026-21728/):
  [frontend limit fix](https://github.com/grafana/tempo/commit/650eb1985a0776789c8564122990f588a742356f).
- [CVE-2026-28377 advisory](https://grafana.com/security/security-advisories/cve-2026-28377/):
  [S3 encryption-key fix](https://github.com/grafana/tempo/commit/bb8ca663db34a0980c9758b40d918fda3b4dbec3).

GitHub ancestry comparisons and the checksum-verified source confirm both fixes
remain present. More directly, the plugin source import closure contains only
Tempo protobuf packages under `pkg/tempopb`. Inspection of the actual ELF Go
function table found 1,497 Tempo functions, all under that same package family;
the affected frontend and S3 server packages are absent. The pseudo-version's
`v1.5.1` prefix alone does not describe the September 2026 revision's patch state.

## Gate behavior

```bash
make lab-runtime-audit
make lab-qualified-publish
```

The gate extracts the plugin from a created but never started image container
and hashes its bytes. Only the exact reviewed findings on the exact image and
binary qualify. A different image, binary, package, dependency version, target,
severity, unknown finding, scanner error or incomplete inventory remains blocking.
Changing the profile requires independent source re-verification and updating
its pinned checksum; it cannot silently broaden an existing assessment.

Private reports live under `.local/evidence/runtime-security/`:

- Per-role `vulnerabilities.json` and `sbom.json` retain the raw Trivy output.
- `grafana/assessment.openvex.json` records the separate local applicability decision.
- `summary.json` records raw counts, assessed findings, binary/profile/VEX hashes
  and unresolved counts. Qualified publication binds the summary checksum into
  the locally signed release envelope.

A successful assessed gate means **raw HIGH=2, CRITICAL=0; not affected=2;
unresolved HIGH=0, CRITICAL=0** for this inventory. It does not mean the raw scan
is clean. There is no ignore-file entry, unfixed-finding exemption, scanner
filter, plugin modification or signature bypass.

## Rechecking the source evidence

The profile records the official plugin/source archive URLs and checksums,
module checksum, upstream fix commits, comparison URLs and fixed-source-file
checksums. Download and verify these exact archives before inspection. The
[Go inspector](../scripts/security/inspect-go-binary.go) reads build information
and the ELF Go function table without executing the plugin:

```bash
go run scripts/security/inspect-go-binary.go /path/to/verified/plugin-binary
```

Use the pinned Go builder in `infra/local-lab/ollama-source.lock.json` if Go is
unavailable locally. In the verified datasource source, `go list -mod=readonly
-deps ./pkg` produces the import closure. Confirm the binary module checksum,
source revision ancestry and both source fixes, and ensure no affected package
appears in either imports or linked functions. Local inspection output is kept
under ignored `.local/evidence/tempo-assessment/`; it contains public code and
synthetic qualification evidence only.

This is local development qualification using a local signing identity. Public
release identity, remote CI execution and production acceptance remain separate.
