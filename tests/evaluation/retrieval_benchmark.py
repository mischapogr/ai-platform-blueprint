"""Synthetic retrieval evaluation; also runnable inside the isolated API image."""

import hashlib
import json
import math
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KS = (1, 3, 5)


def load_corpus(path):
    corpus = json.loads(path.read_text())
    sources = {s["id"]: s for s in corpus["sources"]}
    evidence = {e["id"]: e for e in corpus["evidence"]}
    if len(sources) != len(corpus["sources"]) or len(evidence) != len(corpus["evidence"]):
        raise ValueError("Duplicate source/evidence IDs")
    seen = set()
    for item in evidence.values():
        text = " ".join(sources[item["source"]]["text"].split())
        if not item["quote"] or text.count(item["quote"]) != 1:
            raise ValueError("Evidence must identify one exact normalized text span")
    for query in corpus["queries"]:
        if query["id"] in seen or query["split"] not in {"calibration", "regression"}:
            raise ValueError("Invalid query ID/split")
        seen.add(query["id"])
        if query["collection"] not in {s["collection"] for s in sources.values()}:
            raise ValueError("Unknown query collection")
        if len(set(query["relevant"])) != len(query["relevant"]):
            raise ValueError("Duplicate relevance labels")
        for label in query["relevant"]:
            source = sources[evidence[label]["source"]]
            if source["collection"] != query["collection"]:
                raise ValueError("Relevant evidence belongs to another collection")
    return corpus


def score(query, rows, evidence):
    """Rows contain stable fixture source IDs and the actual retrieved chunk text."""
    expected = set(query["relevant"])
    result = {}
    for k in KS:
        found = set()
        first_rank = None
        for rank, row in enumerate(rows[:k], 1):
            hits = {
                label
                for label in expected
                if evidence[label]["source"] == row["source"]
                and evidence[label]["quote"] in row["content"]
            }
            found.update(hits)
            if hits and first_rank is None:
                first_rank = rank
        result[str(k)] = {
            "recall": len(found) / len(expected) if expected else None,
            "reciprocal_rank": 1 / first_rank if first_rank else (0 if expected else None),
            "false_positive": bool(rows[:k]) if not expected else None,
        }
    return result


def summarize(cases):
    result = {"queries": len(cases)}
    answerable = [c for c in cases if c["answerable"]]
    negatives = [c for c in cases if not c["answerable"]]
    result.update(answerable=len(answerable), unanswerable=len(negatives))
    for k in map(str, KS):
        result[k] = {
            "recall": round(sum(c["metrics"][k]["recall"] for c in answerable) / len(answerable), 6)
            if answerable
            else None,
            "mrr": round(
                sum(c["metrics"][k]["reciprocal_rank"] for c in answerable) / len(answerable), 6
            )
            if answerable
            else None,
            "false_positive_rate": round(
                sum(c["metrics"][k]["false_positive"] for c in negatives) / len(negatives), 6
            )
            if negatives
            else None,
        }
    return result


def gate(report, baseline):
    failures = []
    if report["leakage_count"]:
        failures.append("Collection leakage detected")
    if baseline["corpus_sha256"] != report["corpus_sha256"]:
        failures.append("Corpus changed; review and explicitly record a new baseline")
    if baseline["protocol"] != report["protocol"]:
        failures.append("Evaluation protocol changed; review the baseline")
    for split in ("calibration", "regression"):
        for k in map(str, KS):
            actual = report["summary"][split][k]
            reference = baseline["summary"][split][k]
            for metric in ("recall", "mrr", "false_positive_rate"):
                value, limit = actual[metric], reference[metric]
                if value is None or limit is None or not all(map(math.isfinite, (value, limit))):
                    failures.append(f"Missing metric: {split}/{k}/{metric}")
                elif (metric == "false_positive_rate" and value > limit) or (
                    metric != "false_positive_rate" and value < limit
                ):
                    failures.append(f"Regression: {split}/{k}/{metric}: {value} vs {limit}")
    return failures


def run(provider="offline"):
    from platform_api import db, ingest_job, ingestion, knowledge

    path = HERE / "corpus.json"
    corpus = load_corpus(path)
    evidence = {e["id"]: e for e in corpus["evidence"]}
    collections = {}
    source_ids = {}
    for name in sorted({s["collection"] for s in corpus["sources"]}):
        collection = knowledge.create_collection(
            knowledge.CollectionInput(
                name=f"{provider} {name}",
                embedding_provider=provider,
            )
        )
        collections[name] = collection
    for source in corpus["sources"]:
        namespace = "retrieval-benchmark/" + ("ollama/" if provider == "ollama" else "")
        source_id = uuid.uuid5(uuid.NAMESPACE_URL, namespace + source["id"])
        job_id = uuid.uuid5(source_id, "job")
        token = uuid.uuid5(job_id, "attempt")
        source_ids[str(source_id)] = source
        # Set up a claimed job directly: Redis/supervisor recovery is outside B01.
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO kb_sources (id,collection_id,title,kind,payload,status) "
                "VALUES (%s,%s,%s,'text',%s,'running')",
                (
                    source_id,
                    collections[source["collection"]]["id"],
                    source["title"],
                    source["text"].encode(),
                ),
            )
            conn.execute(
                "INSERT INTO kb_jobs (id,source_id,status,attempt_token,lease_until) "
                "VALUES (%s,%s,'running',%s,now()+interval '240 seconds')",
                (job_id, source_id, token),
            )
        ingest_job.process(job_id, token)
        # The production SQL breaks score ties by UUID. Give fixtures stable UUIDs
        # after real ingestion so reimports do not randomly reorder tied scores.
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id,ordinal,content FROM kb_chunks WHERE source_id=%s", (source_id,)
            ).fetchall()
            if not rows:
                raise ValueError("Ingestion produced no chunks")
            for row in rows:
                stable_id = uuid.uuid5(source_id, f"{row['ordinal']}:{row['content']}")
                conn.execute("UPDATE kb_chunks SET id=%s WHERE id=%s", (stable_id, row["id"]))
            for label in [e for e in evidence.values() if e["source"] == source["id"]]:
                if not any(label["quote"] in row["content"] for row in rows):
                    raise ValueError("Evidence span does not fit any actual chunk")
    cases = []
    for query in corpus["queries"]:
        started = time.perf_counter()
        retrieved = knowledge.retrieve(query["question"], collections[query["collection"]], 5)
        elapsed = time.perf_counter() - started
        rows = [
            {
                "source": source_ids[str(r["source_id"])]["id"],
                "chunk_id": str(r["id"]),
                "content": r["content"],
                "score": float(r["score"]),
                "collection": source_ids[str(r["source_id"])]["collection"],
            }
            for r in retrieved
        ]
        cases.append(
            {
                "id": query["id"],
                "split": query["split"],
                "category": query["category"],
                "answerable": bool(query["relevant"]),
                "seconds": elapsed,
                "leakage": any(r["collection"] != query["collection"] for r in rows),
                "retrieved": [
                    {key: r[key] for key in ("source", "chunk_id", "score")} for r in rows
                ],
                "metrics": score(query, rows, evidence),
            }
        )
    return {
        "corpus_version": corpus["version"],
        "corpus_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "protocol": "evidence-span-v1;macro-recall;first-relevant-mrr;top5-prefix;stable-uuid-ties",
        "configuration": {
            "provider": provider,
            "model": collection["embedding_model"],
            "dimensions": collection["dimensions"],
            "digest": collection.get("embedding_digest"),
            "endpoint": collection.get("embedding_endpoint"),
            "profile": collection.get("embedding_profile", "plain"),
            "chunk_characters": ingestion.CHUNK_CHARACTERS,
            "overlap_characters": ingestion.CHUNK_OVERLAP_CHARACTERS,
            "score_cutoff": knowledge.RETRIEVAL_SCORE_CUTOFF,
            "k": KS,
        },
        "leakage_count": sum(c["leakage"] for c in cases),
        "summary": {
            split: summarize([c for c in cases if c["split"] == split])
            for split in ("calibration", "regression")
        },
        "categories": {
            category: summarize([c for c in cases if c["category"] == category])
            for category in sorted({c["category"] for c in cases})
        },
        "cases": cases,
    }


if __name__ == "__main__":
    # Public fixture password, on a private tmpfs in the disposable runner.
    Path("/tmp/benchmark-password").write_text("synthetic-benchmark-only")  # noqa: S108
    print(json.dumps(run()))
