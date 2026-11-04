# Dokumentationsübersicht

Die Diagramme sind als Mermaid direkt in Markdown gepflegt und auf GitHub renderbar.
Sie beschreiben das lokale MVP oder ausdrücklich als offen markierte Ausbaustufen.

| Dokument | Inhalt und Visualisierung |
|---|---|
| [Knowledge Workspace](KNOWLEDGE-WORKSPACE.md) | Collections, frontend, imports, model configuration and processing policies |
| [Ingestion runbook](runbooks/KNOWLEDGE-INGESTION.md) | Queue, worker, retry and source errors |
| [Architektur](ARCHITECTURE.md) | Dienste, Netzgrenzen und Retrieval-Sequenz |
| [Roadmap](ROADMAP.md) | Entwicklungsstufen und Security-Gate |
| [Capability backlog](BACKLOG.md) | Prioritized work packages, acceptance criteria, estimates and dependencies |
| [Retrieval evaluation](RETRIEVAL-EVALUATION.md) | Synthetic quality benchmark, metric definitions, measured baseline and reproduction |
| [Tracing](../observability/tempo/README.md) | Optional Tempo deployment, request/job correlation and telemetry privacy |
| [Local models](LOCAL-MODELS.md) | Optional Ollama provider, immutable embedding identity and live CPU qualification |
| [Answer evaluation](ANSWER-EVALUATION.md) | Fixed/retrieved evidence, deterministic checks and human review rubric |
| [Local observability](LOCAL-OBSERVABILITY.md) | Operator dashboard, Loki-to-Tempo troubleshooting and workflow checks |
| [Requirements audit](REQUIREMENTS-AUDIT.md) | Coverage, evidence gaps and prioritized operator improvements |
| [Local infrastructure lab](LOCAL-LAB.md) | Local OCI registry, signed deployment, TLS/alerts and fresh-VM recovery |
| [Release qualification](RELEASE-QUALIFICATION.md) | Image/SBOM/provenance bundle, local signatures and rollback compatibility |
| [Tempo source assessment](TEMPO-SOURCE-ASSESSMENT.md) | Exact-artifact applicability evidence, retained raw findings and fail-closed qualification |
| [Threat Model](THREAT-MODEL.md) | Vertrauensgrenzen, Datenflüsse und Risiken |
| [Disaster Recovery](DISASTER-RECOVERY.md) | Verschlüsselter Backup-Pfad und isolierter Restore |
| [DB-Runbook](runbooks/DATABASE-UNAVAILABLE.md) | Ausfall, Erkennung, Alert und Recovery |
| [Validierung](VALIDATION.md) | Ausgeführte Prüfungen und Nachweisgrenzen |
| [Security-Scan](SECURITY-SCAN.md) | Offene Scanbefunde und Reproduktion |

Start und Demo-Befehle: [Repository-README](../README.md).
