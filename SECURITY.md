# Security policy

This project is an experimental, single-owner localhost reference. No stable
release or production security approval is currently claimed. Known image scan
findings and their exact scope are recorded in [the scan snapshot](docs/SECURITY-SCAN.md).

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/mischapogr/ai-platform-blueprint/security/advisories/new)
if it has been enabled for the repository. If that option is unavailable, open an
issue asking for a private reporting channel without publishing exploit details,
credentials or document contents. No response-time commitment is currently offered.

Provide the affected commit/version, a synthetic reproduction, the relevant trust
boundary and the observed impact. Use public issue templates for ordinary bugs,
not for disclosing an unpatched vulnerability or private source material.

## Scope

The supported configuration binds published ports to localhost. Workspace routes
use an owner session; the legacy synthetic demo endpoints remain unauthenticated.
Collections constrain retrieval context and processing policy, not independent
multi-user authorization. Public hosting and multi-tenant isolation are not qualified.

Provider credentials, local source manifests, corpora and evidence are excluded
from Git and image build contexts. Never attach real versions of those files to
issues or pull requests. CI artifacts are limited to the image scan report.
