import copy
import json
import sys
from pathlib import Path

import pytest

EVALUATION = Path(__file__).resolve().parents[1] / "evaluation"
sys.path.insert(0, str(EVALUATION))
from retrieval_benchmark import gate, load_corpus, score, summarize  # noqa: E402


def test_evidence_recall_deduplicates_overlap_and_requires_correct_source():
    evidence = {
        "one": {"source": "a", "quote": "first fact"},
        "two": {"source": "b", "quote": "second fact"},
    }
    query = {"relevant": ["one", "two"]}
    rows = [
        {"source": "wrong", "content": "first fact"},
        {"source": "a", "content": "first fact"},
        {"source": "a", "content": "first fact repeated overlapping chunk"},
        {"source": "b", "content": "second fact"},
    ]
    result = score(query, rows, evidence)
    assert result["1"]["recall"] == 0
    assert result["3"]["recall"] == 0.5
    assert result["5"]["recall"] == 1
    assert result["5"]["reciprocal_rank"] == 0.5
    assert score(query, [], evidence)["5"]["reciprocal_rank"] == 0


def test_unanswerable_and_answerable_denominators_are_separate():
    cases = [
        {
            "answerable": True,
            "metrics": score(
                {"relevant": ["one"]},
                [{"source": "a", "content": "fact"}],
                {"one": {"source": "a", "quote": "fact"}},
            ),
        },
        {"answerable": False, "metrics": score({"relevant": []}, [], {})},
        {
            "answerable": False,
            "metrics": score(
                {"relevant": []},
                [{"source": "a", "content": "unrelated"}],
                {},
            ),
        },
    ]
    summary = summarize(cases)
    assert summary["5"] == {"recall": 1, "mrr": 1, "false_positive_rate": 0.5}
    assert summarize([])["5"] == {"recall": None, "mrr": None, "false_positive_rate": None}


def test_gate_rejects_quality_loss_and_leakage_independently():
    metrics = {str(k): {"recall": 0.8, "mrr": 0.7, "false_positive_rate": 0.5} for k in (1, 3, 5)}
    baseline = {
        "corpus_sha256": "corpus",
        "protocol": "v1",
        "summary": {"calibration": metrics, "regression": metrics},
    }
    report = {**copy.deepcopy(baseline), "leakage_count": 0}
    assert gate(report, baseline) == []
    report["leakage_count"] = 1
    assert "Collection leakage detected" in gate(report, baseline)
    report["leakage_count"] = 0
    report["summary"]["regression"]["5"]["recall"] = 0.6
    assert any("regression/5/recall" in f for f in gate(report, baseline))
    report["summary"]["regression"]["5"]["false_positive_rate"] = 1
    assert any("false_positive_rate" in f for f in gate(report, baseline))
    report["corpus_sha256"] = "changed"
    assert any("Corpus changed" in f for f in gate(report, baseline))
    report["summary"]["regression"]["1"]["mrr"] = float("nan")
    assert any("Missing metric" in f for f in gate(report, baseline))


def test_fixture_labels_are_valid_and_invalid_labels_fail(tmp_path):
    corpus = load_corpus(EVALUATION / "corpus.json")
    assert 40 <= len(corpus["queries"]) <= 60
    for split in ("calibration", "regression"):
        queries = [q for q in corpus["queries"] if q["split"] == split]
        assert any(q["relevant"] for q in queries)
        assert any(not q["relevant"] for q in queries)
    corpus["evidence"][0]["quote"] = "missing evidence"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="exact normalized text span"):
        load_corpus(invalid)
