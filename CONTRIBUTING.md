# Contributing

Use a small, reviewable change with a synthetic reproduction. Keep real documents,
source manifests, API keys, deployment inventories and local evidence outside Git.

## Local checks

```bash
make init
make lint test
make up
make knowledge-test
make integration
make failure-test
make load-test
```

Run the integration and failure scenarios sequentially against the disposable
local stack. `make model-smoke` performs a real API request and is opt-in; it is
not part of CI. The strict image-security gate currently has open findings; see
[SECURITY-SCAN.md](docs/SECURITY-SCAN.md). Do not suppress findings merely to make CI green.

## Commits and versions

Use Conventional Commits:

- `feat: ...` for a new capability.
- `fix: ...` for a correction.
- `docs: ...`, `test: ...`, `ci: ...` and `chore: ...` for their respective changes.
- Add `!` and a `BREAKING CHANGE:` footer for an incompatible change.

The project currently identifies as `0.1.0` and is under active development.
Future release versions follow Semantic Versioning: compatible fixes increment
PATCH, compatible features increment MINOR, and incompatible stable API changes
increment MAJOR. During `0.x`, incompatible changes increment MINOR and must be
documented. A commit is not a release; there is no automated release/tagging
workflow yet. Release qualification remains separate from local test results.

Keep Markdown documentation filenames uppercase, with the `.md` extension.
Update links and runbook code references when renaming files. Mermaid diagrams
should describe implemented behavior or explicitly label planned work.

## License

The project uses [Apache-2.0](LICENSE); attribution is in [NOTICE](NOTICE).
Keep third-party attribution and license notices intact. Imported source documents
are not relicensed by contributing application code.
