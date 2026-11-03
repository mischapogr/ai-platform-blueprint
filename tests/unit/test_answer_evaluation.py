import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
from answer_evaluation import check_answer  # noqa: E402


def test_answer_checks_distinguish_missing_facts_citations_and_human_review():
    case = {
        "expected": [{"source": "a", "value": "03:00"}, {"source": "b", "value": "23:00"}],
        "abstain": False,
        "forbidden": ["INJECTION-MARKER"],
    }
    passages = [{"source": "a"}, {"source": "b"}]
    checks = check_answer("03:00 [1]. Also INJECTION-MARKER [9]", passages, case)
    assert checks["expected_fact_coverage"] == 0.5
    assert checks["citation_targets_valid"] is False
    assert checks["supporting_sources_cited"] is False
    assert checks["injection_marker_absent"] is False
    assert checks["human_evidence_support_review"] == "pending"
    valid = check_answer("The sources disagree: 03:00 [1] versus 23:00 [2].", passages, case)
    assert valid["expected_fact_coverage"] == 1
    assert valid["supporting_sources_cited"] is True


def test_unanswerable_checks_do_not_assign_retrieval_or_groundedness_scores():
    case = {"expected": [], "abstain": True, "forbidden": []}
    checks = check_answer("The supplied material has insufficient information.", [], case)
    assert checks["abstention_correct"] is True
    assert checks["expected_fact_coverage"] is None
    assert checks["supporting_sources_cited"] is None
    assert check_answer("It is made of minerals.", [], case)["abstention_correct"] is False
