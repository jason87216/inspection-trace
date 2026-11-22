import pytest

from inspection_trace.investigation import Case, Evidence, Finding, score, select_cases
from scripts.evaluate_investigation import endpoint


def test_correct_cause_requires_actual_relevant_evidence_to_be_grounded() -> None:
    case = Case("L1", "EV_QTIME_OVER", "L1")
    evidence = {
        "E1": Evidence("get_lot_history", {"qtime": [{"lot_no": "L1", "status": "over_limit"}]}),
        "E2": Evidence("check_qtime", {"items": [{"lot_no": "L2", "status": "over_limit"}]}),
    }
    finding = Finding(cause="EV_QTIME_OVER", entity="L1", evidence_ids=["E1"], explanation="wait")
    assert score(case, finding, evidence).grounded_hit
    unrelated = score(case, finding.model_copy(update={"evidence_ids": ["E2"]}), evidence)
    assert unrelated.root_hit and not unrelated.grounded_hit and unrelated.unsupported_conclusion
    fabricated = score(case, finding.model_copy(update={"evidence_ids": ["E1", "E9"]}), evidence)
    assert fabricated.invalid_citations == 1
    assert not fabricated.grounded_hit


def test_case_selection_keeps_first_occurrence_for_each_root_cause() -> None:
    events = [
        {"event": "EV_DRIFT", "lots": ["L0", "L1"], "machine": "LDR-01"},
        {"event": "EV_DRIFT", "lots": ["L2"], "machine": "LDR-01"},
        {"event": "EV_OOS_SPIKE", "lot_no": "L3"},
        {"event": "EV_MAT_SHIFT", "lots": ["L4"], "material_lot": "ABF-01"},
    ]
    assert select_cases(events, 1) == [
        Case("L1", "EV_DRIFT", "LDR-01"),
        Case("L4", "EV_MAT_SHIFT", "ABF-01"),
    ]


def test_provider_requires_explicit_remote_https_and_keeps_credentials_out_of_url() -> None:
    assert endpoint("http://127.0.0.1:8080/v1", False).endswith("/v1/chat/completions")
    assert endpoint("https://model.example/v1", True).endswith("/v1/chat/completions")
    for url, remote in (
        ("https://model.example/v1", False),
        ("http://model.example/v1", True),
        ("http://secret@localhost/v1", False),
    ):
        with pytest.raises(ValueError):
            endpoint(url, remote)
