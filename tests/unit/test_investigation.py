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
    wrong_lot = finding.model_copy(update={"entity": "L2", "evidence_ids": ["E2"]})
    assert score(case, wrong_lot, evidence).unsupported_conclusion


@pytest.mark.parametrize(
    ("cause", "machine", "item", "signal", "unrelated_signal"),
    [
        ("EV_DRIFT", "LDR-01", "VIA_TOP_D", "I_EIGHT_SAME_SIDE", "MR_OUTSIDE_LIMIT"),
        ("EV_BATH_AGING", "PLT-01", "PLT_CU", "MR_OUTSIDE_LIMIT", "I_EIGHT_SAME_SIDE"),
    ],
)
def test_machine_findings_require_a_matching_lot_and_signal(
    cause: str, machine: str, item: str, signal: str, unrelated_signal: str
) -> None:
    case = Case("L1", cause, machine, (item,), -1 if cause == "EV_DRIFT" else 0)
    finding = Finding.model_validate(
        {"cause": cause, "entity": machine, "evidence_ids": ["E1"], "explanation": "SPC"}
    )
    record = {
        "id": 17,
        "lot_no": "L2",
        "machine_code": machine,
        "item_code": item,
        "value": 14.0,
        "out_of_spec": False,
    }
    group = {
        "group": machine,
        "count": 60,
        "mean": 14.0,
        "out_of_spec_count": 0,
        "out_of_spec_rate": 0.0,
        "evidence_record_id": 17,
    }
    point = {"id": 18, "lot_no": "L1", "value": 12.0, "signals": [signal]}
    points = [point, {**point, "id": 19, "lot_no": "L2"}]
    chart = {
        "machine_code": machine,
        "item_code": item,
        "individual_limits": {"center": 14.0, "lower": 11.0, "upper": 17.0},
        "signal_count": 2,
        "signals": points,
    }
    for citation in (
        Evidence("query_records", {"items": [record], "total": 1}),
        Evidence("query_records", {"items": [{**record, "lot_no": "L1"}], "total": 1}),
        Evidence("compare_groups", {"result": [group]}, {"group_by": "machine", "item_code": item}),
        Evidence("spc_status", {**chart, "signals": points[1:]}),
        Evidence("spc_status", {**chart, "signals": [{**point, "signals": [unrelated_signal]}]}),
        Evidence("spc_status", {**chart, "item_code": "OTHER"}),
        Evidence("spc_status", {**chart, "machine_code": "OTHER"}),
    ):
        result = score(case, finding, {"E1": citation})
        assert result.root_hit and not result.grounded_hit and result.unsupported_conclusion
    assert score(case, finding, {"E1": Evidence("spc_status", chart)}).grounded_hit
    single_point = {**point, "signals": ["I_OUTSIDE_3SIGMA" if cause == "EV_DRIFT" else signal]}
    isolated = Evidence("spc_status", {**chart, "signal_count": 1, "signals": [single_point]})
    assert not score(case, finding, {"E1": isolated}).grounded_hit


def test_material_findings_require_linked_lot_and_a_measurement_shift() -> None:
    case = Case("L1", "EV_MAT_SHIFT", "ABF-01", ("CUSTOM_THK",), 1)
    finding = Finding(
        cause="EV_MAT_SHIFT", entity="ABF-01", evidence_ids=["E1", "E2"], explanation="ABF step"
    )
    trace = {
        "lot_no": "L1",
        "direction": "up",
        "links": [
            {"parent_lot_no": "L0", "child_lot_no": "L1", "relation": "split", "depth": 1},
            {"parent_lot_no": "ABF-01", "child_lot_no": "L0", "relation": "consume", "depth": 2},
        ],
    }
    chart = {
        "machine_code": "LAM-01",
        "item_code": "CUSTOM_THK",
        "individual_limits": {"center": 28.0, "lower": 27.0, "upper": 29.0},
        "signals": [{"id": 20, "lot_no": "L1", "value": 30.0, "signals": ["I_EIGHT_SAME_SIDE"]}],
    }
    evidence = {"E1": Evidence("trace_lot", trace), "E2": Evidence("spc_status", chart)}
    assert score(case, finding, evidence).grounded_hit
    assert not score(
        case, finding.model_copy(update={"evidence_ids": ["E1"]}), evidence
    ).grounded_hit
    downward = {"id": 20, "lot_no": "L1", "value": 26.0, "signals": ["I_EIGHT_SAME_SIDE"]}
    wrong_direction = {**evidence, "E2": Evidence("spc_status", {**chart, "signals": [downward]})}
    assert not score(case, finding, wrong_direction).grounded_hit
    evidence["E1"] = Evidence("trace_lot", {**trace, "lot_no": "L2"})
    assert not score(case, finding, evidence).grounded_hit


def test_case_selection_keeps_first_occurrence_for_each_root_cause() -> None:
    events = [
        {
            "event": "EV_DRIFT",
            "lots": ["L0", "L1"],
            "machine": "LDR-01",
            "items": ["VIA_TOP_D", "VIA_BOT_D"],
            "rate_per_lot": -0.35,
        },
        {"event": "EV_DRIFT", "lots": ["L2"], "machine": "LDR-01"},
        {"event": "EV_OOS_SPIKE", "lot_no": "L3"},
        {
            "event": "EV_MAT_SHIFT",
            "lots": ["L4"],
            "material_lot": "ABF-01",
            "item": "CUSTOM_THK",
            "delta": 1.8,
        },
    ]
    assert select_cases(events, 1) == [
        Case("L1", "EV_DRIFT", "LDR-01", ("VIA_TOP_D", "VIA_BOT_D"), -1),
        Case("L4", "EV_MAT_SHIFT", "ABF-01", ("CUSTOM_THK",), 1),
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
