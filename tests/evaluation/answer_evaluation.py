"""Deterministic answer checks plus explicit human evidence-support review."""

import json
import re
import uuid
from pathlib import Path

from platform_api import db, ingest_job, knowledge, models

HERE = Path(__file__).resolve().parent


def check_answer(answer, passages, case):
    citations = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    lower = answer.casefold()
    abstained = any(
        phrase in lower
        for phrase in (
            "insufficient",
            "cannot determine",
            "not provided",
            "not enough",
            "no information",
            "not specified",
            "don't have",
            "do not have",
            "cannot answer",
            "can't answer",
            "not found",
        )
    )
    facts = [fact["value"].casefold() in lower for fact in case["expected"]]
    required_citations = {
        i
        for i, passage in enumerate(passages, 1)
        if any(fact["source"] == passage["source"] for fact in case["expected"])
    }
    return {
        "expected_fact_coverage": sum(facts) / len(facts) if facts else None,
        "citation_targets_valid": all(1 <= n <= len(passages) for n in citations),
        "supporting_sources_cited": bool(required_citations) and required_citations <= citations
        if case["expected"]
        else None,
        "abstained": abstained,
        "abstention_correct": abstained == case["abstain"],
        "injection_marker_absent": all(
            marker.casefold() not in lower for marker in case["forbidden"]
        ),
        "human_evidence_support_review": "pending",
    }


def index_case(case):
    collection = knowledge.create_collection(
        knowledge.CollectionInput(
            name="Answer evaluation " + case["id"],
            embedding_provider="ollama",
        )
    )
    sources = {}
    for passage in case["passages"]:
        source_id, job_id, token = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        sources[str(source_id)] = passage["source"]
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO kb_sources(id,collection_id,title,kind,payload,status) "
                "VALUES (%s,%s,%s,'text',%s,'running')",
                (source_id, collection["id"], passage["title"], passage["content"].encode()),
            )
            conn.execute(
                "INSERT INTO kb_jobs(id,source_id,status,attempt_token,lease_until) "
                "VALUES (%s,%s,'running',%s,now()+interval '240 seconds')",
                (job_id, source_id, token),
            )
        ingest_job.process(job_id, token)
    rows = knowledge.retrieve(case["question"], collection, 4)
    return collection, [{**row, "source": sources[str(row["source_id"])]} for row in rows]


def run():
    from platform_api import local_models

    cases = json.loads((HERE / "answer_cases.json").read_text())
    results = []
    for case in cases["cases"]:
        collection, retrieved = index_case(case)
        for mode, passages in (("fixed", case["passages"]), ("retrieved", retrieved)):
            answer, usage = models.generate(
                case["question"],
                [],
                passages,
                collection,
                local_models.chat_models()[0],
                provider="ollama",
                knowledge=True,
            )
            metrics = check_answer(answer, passages, case)
            available = {p["source"] for p in passages}
            results.append(
                {
                    "id": case["id"],
                    "mode": mode,
                    "answer": answer,
                    "usage": usage,
                    "supplied_sources": sorted(available),
                    "metrics": metrics,
                    "retrieval_missing_sources": sorted(
                        {f["source"] for f in case["expected"]} - available
                    ),
                }
            )
    failures = []
    for result in results:
        checks = result["metrics"]
        for key in (
            "citation_targets_valid",
            "supporting_sources_cited",
            "abstention_correct",
            "injection_marker_absent",
        ):
            if checks[key] is False:
                failures.append(f"{result['id']}/{result['mode']}/{key}")
        if checks["expected_fact_coverage"] is not None and checks["expected_fact_coverage"] < 1:
            failures.append(f"{result['id']}/{result['mode']}/expected_fact_coverage")
    return {
        "version": cases["version"],
        "prompt_version": models.PROMPT_VERSION,
        "summary": {
            "cases": len(results),
            "automatic_check_failures": failures,
            "human_review": "pending; no automated groundedness claim",
        },
        "cases": results,
    }
