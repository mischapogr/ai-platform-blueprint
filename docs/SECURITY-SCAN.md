# Lokaler Security-Scan

Stand: 2026-11-02. Dies ist ein begrenzter Scanbefund, kein Security-Audit.

## Ergebnis

- Runtime-Dependency-Scan mit pip-audit und erzwungenen Hashes: keine bekannten Befunde.
- API-Image mit Trivy 0.76.1: **45 HIGH-Paketbefunde, 9 verschiedene CVE-IDs, 0 CRITICAL**.
- Alle 45 Einträge haben im Scan kein `FixedVersion`-Feld. Das beweist keine fehlende Ausnutzbarkeit.
- HIGH/CRITICAL-Befunde in den Python-Paketen des Images: 0.
- Das strenge Image-Gate liefert Exit 1. Es gibt keine Unterdrückung, Allowlist oder Ausnahme für ungefixte Befunde.

Image-ID: `sha256:94e6d7e52a52a13dc4cd715187d64fe00ddb00be7560bdd8347d633c8978b1e4`.
Dieser Snapshot bezieht sich auf die genannte Image-ID; spätere Builds sind
nicht automatisch durch ihn abgedeckt.
Basis: `python:3.12-slim-trixie`, im Scan Debian 13.7.
Scanzeit: `2026-11-02T20:34:14.961405145Z`.

## Zusammengefasste offene Befunde

| Advisory | Betroffene Pakete | Severity | Fix im Scan |
|---|---|---|---|
| CVE-2025-69720 | libncursesw6, libtinfo6, ncurses-base, ncurses-bin | HIGH | keiner angegeben |
| CVE-2026-119373 | libsqlite3-0 | HIGH | keiner angegeben |
| CVE-2026-16742 | libsystemd0, libudev1 | HIGH | keiner angegeben |
| CVE-2026-54369 | libacl1 | HIGH | keiner angegeben |
| CVE-2026-76642 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | HIGH | keiner angegeben |
| CVE-2026-78408 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | HIGH | keiner angegeben |
| CVE-2026-78409 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | HIGH | keiner angegeben |
| CVE-2026-78410 | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | HIGH | keiner angegeben |
| CVE-2026-9538 | perl-base | HIGH | keiner angegeben |

## Reproduktion und Grenze

```bash
make scan
make image-scan    # erwarteter Exit 1 für diesen Stand
```

Der vollständige lokale JSON-Bericht liegt ignoriert unter `.local/evidence/trivy.json`.
Das Scan-Skript exportiert das API-Image; der Scanner erhält keinen Docker-Socket.
CI verwendet denselben Befehl und lädt ausschließlich den Scanbericht als Artefakt hoch.
Ein grüner CI-/Release-Status wird nicht behauptet. Ein GitHub-Lauf wurde hier nicht ausgeführt.

Die übrigen Service-Images sind in diesem Nachweis nicht auf CVEs geprüft.
MEDIUM/LOW-Befunde, IaC-/Secret-Scans, Reachability-Analyse, Hosthärtung und
ein unabhängiger Review sind nicht abgedeckt. Vor öffentlichem Betrieb sind die
offenen Befunde zu triagieren und zu beheben oder ausdrücklich begründet freizugeben.
Keine solche Freigabe ist erfolgt. Den Bericht nach Updates erneut erzeugen.
