# Retrieval evaluation

This is the B01 synthetic retrieval baseline. It measures evidence retrieval,
not generated-answer quality or semantic model performance.

## Run

After `make init`, with Docker available:

```bash
make retrieval-eval
```

The command builds a dedicated `ai-platform-blueprint-retrieval-eval:local` image
from the locked application dependencies. It creates uniquely named database and
runner containers on an internal Docker network, with no published ports. The
database uses temporary memory-backed storage. Only the evaluation fixtures and
application source are mounted, read-only; workspace secrets, uploads, provider
credentials and existing database volumes are not mounted. Image construction
and initial image pulls can require internet access. Runtime model calls are not
part of this benchmark.

The runner invokes the real collection creation, ingestion processing, chunking,
embedding and `knowledge.retrieve` functions. Claimed jobs are inserted directly;
HTTP/session behavior, Redis wakeups, the worker supervisor and lease recovery
are outside this test's scope. Containers and the network are removed in cleanup;
the dedicated image and Docker build cache remain available for subsequent runs.
The two containers have memory limits totaling 768 MiB, plus host/build overhead.

Full JSON reports go to ignored `.local/evidence/ai-blueprint-eval-*.json` files.
They contain per-case metrics, stable source/chunk IDs, scores, retrieval timing,
corpus and source hashes, code revision and image identities. An uncommitted
checkout is identified by its source hash in addition to the parent revision.
Timing is diagnostic and has no performance acceptance threshold.

## Dataset and metric definitions

[corpus.json](../tests/evaluation/corpus.json) contains 14 fictional sources in two
collections and 48 questions: 24 calibration and 24 regression cases. Each split
has 19 answerable and five unanswerable questions. Cases include lexical queries,
paraphrases, multi-evidence questions, shared vocabulary distractors and collection
traps. Two sources exceed one chunk. These public regression questions are fixed
holdout cases for future changes, not a statistically independent or secret test
set; both splits share the source corpus.

Relevance labels identify an exact quote within a stable source. A retrieved chunk
is relevant when it comes from that source and contains the entire labeled quote.
Each quote must occur exactly once in normalized source text and fit an actual
chunk. Invalid labels fail evaluation rather than silently lowering scores. This
strict span test is a reproducible proxy for evidence support, not a semantic
judge. New chunking methods may need an explicit relevance-mapping revision.

- **Recall@K:** fraction of the question's labeled evidence spans found in the
  first K chunks, averaged across answerable questions. Overlapping chunks do not
  double-count the same evidence. A zero-result answerable question scores zero.
- **MRR@K:** reciprocal rank of the first chunk containing any relevant evidence,
  averaged across answerable questions; zero if none is found. Multi-evidence
  completeness is captured by recall, rather than MRR.
- **Unanswerable false-positive rate@K:** fraction of unanswerable questions with
  any returned chunk. These questions do not enter recall or MRR denominators.
- **Collection leakage:** any returned source outside the requested collection;
  always fails the run, independently of aggregate relevance metrics.

K is 1, 3 and 5, computed as prefixes of the application's actual top-five result.
No evaluation-side reranking or filtering is applied. After actual ingestion, the
fixture chunk UUIDs are normalized deterministically from source ID, ordinal and
content. The application's existing score/UUID tie order is then reproducible
across fresh databases. This does not qualify production tie ordering after a
reimport with randomly assigned UUIDs. Labels and scoring never depend on chunk
UUIDs; changing a chunk's contents can still change its tie order.

## Initial measured baseline — 2026-11-03

Local Docker execution used hash-v1, 128 dimensions, 1,200-character chunks,
150-character overlap and the existing 0.1 retrieval cutoff. No algorithm or
threshold was tuned against these results.

| Split | K | Recall | MRR | Unanswerable false-positive rate |
|---|---|---|---|---|
| Calibration | 1 | 0.815789 | 0.842105 | 1.000000 |
| Calibration | 3 | 0.894737 | 0.868421 | 1.000000 |
| Calibration | 5 | 0.894737 | 0.868421 | 1.000000 |
| Regression | 1 | 0.631579 | 0.631579 | 0.800000 |
| Regression | 3 | 0.789474 | 0.710526 | 0.800000 |
| Regression | 5 | 0.815789 | 0.721053 | 0.800000 |

No collection leakage was observed. Nine of ten unanswerable questions returned
passages. The current lexical hash search therefore needs better abstention;
this baseline is a starting measurement, not an acceptable production quality
target. The small synthetic dataset supports regression detection, not a claim
about real user corpora.

## Regression gate and baseline changes

[baseline.json](../tests/evaluation/baseline.json) stores the measured values. The
default command fails if either split loses recall/MRR or increases the
unanswerable false-positive rate at any supported K. Comparison uses six decimal
places. It also fails on corpus/protocol changes, missing/nonfinite metrics and
collection leakage. These initial floors/ceilings preserve observed behavior;
the weak false-positive ceiling is not a quality endorsement. Aggregate gates can
hide offsetting per-case changes, so review per-case reports when tuning retrieval.

Baseline replacement is explicit and separate from the normal CI gate:

```bash
.venv/bin/python scripts/evaluate-retrieval.py --record-baseline
```

Review the dataset/protocol change and measured results before accepting the
replacement; do not regenerate a baseline solely to conceal a regression. Corpus,
model, chunking and cutoff settings remain in reports so later semantic or hybrid
experiments can be compared transparently. Keep public fixtures synthetic and
real-corpus evaluation in private local storage.
