"""Benchmark cases and scoring kept separate from model input."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

type Cause = Literal["EV_QTIME_OVER", "EV_DRIFT", "EV_MAT_SHIFT", "EV_BATH_AGING", "UNKNOWN"]
type EvidenceId = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$")]

CAUSES = {
    "EV_QTIME_OVER": ("lot_no", "lot_no"),
    "EV_DRIFT": ("lots", "machine"),
    "EV_MAT_SHIFT": ("lots", "material_lot"),
    "EV_BATH_AGING": ("lots", "machine"),
}


@dataclass(frozen=True)
class Case:
    lot_no: str
    cause: str
    entity: str
    item_codes: tuple[str, ...] = ()
    shift_direction: int = 0


class Finding(BaseModel):
    cause: Cause
    entity: Annotated[str, Field(max_length=40)] | None
    evidence_ids: Annotated[list[EvidenceId], Field(max_length=8)]
    explanation: Annotated[str, Field(min_length=1, max_length=1500)]


@dataclass(frozen=True)
class Evidence:
    tool: str
    result: dict[str, object]
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Score:
    root_hit: bool
    grounded_hit: bool
    invalid_citations: int
    unsupported_conclusion: bool


def select_cases(events: Iterable[dict[str, object]], per_type: int) -> list[Case]:
    cases: list[Case] = []
    used: dict[str, int] = dict.fromkeys(CAUSES, 0)
    for event in events:
        label = str(event.get("event"))
        if label not in CAUSES or used[label] >= per_type:
            continue
        lot_key, entity_key = CAUSES[label]
        lots = event.get(lot_key)
        lot = lots if isinstance(lots, str) else None
        if isinstance(lots, list) and lots:
            # Material shifts start at the first lot; gradual events grow across the segment.
            lot = lots[0] if label == "EV_MAT_SHIFT" else lots[-1]
        entity = event.get(entity_key)
        if isinstance(lot, str) and isinstance(entity, str):
            items = event.get("items", event.get("item")) if label != "EV_QTIME_OVER" else None
            if isinstance(items, str):
                items = [items]
            item_codes = (
                tuple(item for item in items if isinstance(item, str))
                if isinstance(items, list)
                else ()
            )
            delta = event.get("rate_per_lot" if label == "EV_DRIFT" else "delta")
            direction = int(delta > 0) - int(delta < 0) if isinstance(delta, (int, float)) else 0
            cases.append(Case(lot, label, entity, item_codes, direction))
            used[label] += 1
    return cases


def _rows(result: dict[str, object], key: str) -> Iterable[dict[str, object]]:
    rows = result.get(key)
    if isinstance(rows, list):
        yield from (row for row in rows if isinstance(row, dict))


def _linked_material(lot_no: str, material: str, trace: dict[str, object]) -> bool:
    if trace.get("lot_no") != lot_no or trace.get("direction") != "up":
        return False
    links = list(_rows(trace, "links"))
    split_ancestors = {lot_no}
    # The trace service returns links in increasing depth order.
    for link in links:
        parent = link.get("parent_lot_no")
        if (
            isinstance(parent, str)
            and link.get("relation") == "split"
            and link.get("child_lot_no") in split_ancestors
        ):
            split_ancestors.add(parent)
    return any(
        link.get("relation") == "consume"
        and link.get("parent_lot_no") == material
        and link.get("child_lot_no") in split_ancestors
        for link in links
    )


def _signal_lots(case: Case, chart: dict[str, object], signal: str) -> set[str]:
    lots: set[str] = set()
    limits = chart.get("individual_limits")
    center = limits.get("center") if isinstance(limits, dict) else None
    for point in _rows(chart, "signals"):
        lot, value, signals = point.get("lot_no"), point.get("value"), point.get("signals")
        if not (isinstance(lot, str) and isinstance(signals, list) and signal in signals):
            continue
        if signal.startswith("I_") and case.shift_direction:
            if not isinstance(center, (int, float)) or not isinstance(value, (int, float)):
                continue
            if (value - center) * case.shift_direction <= 0:
                continue
        lots.add(lot)
    return lots


def _supports(case: Case, finding: Finding, evidence: list[Evidence]) -> bool:
    entity = finding.entity
    if entity is None or finding.cause == "UNKNOWN":
        return False
    if finding.cause == "EV_QTIME_OVER":
        return entity == case.lot_no and any(
            row.get("lot_no") == entity and row.get("status") == "over_limit"
            for item in evidence
            if item.tool in {"get_lot_history", "check_qtime"}
            for row in _rows(item.result, "qtime" if item.tool == "get_lot_history" else "items")
        )
    if finding.cause == "EV_MAT_SHIFT" and not any(
        item.tool == "trace_lot" and _linked_material(case.lot_no, entity, item.result)
        for item in evidence
    ):
        return False

    for item in evidence:
        chart = item.result
        if item.tool != "spc_status" or chart.get("item_code") not in case.item_codes:
            continue
        if finding.cause != "EV_MAT_SHIFT" and chart.get("machine_code") != entity:
            continue
        if finding.cause == "EV_BATH_AGING":
            lots = _signal_lots(case, chart, "MR_OUTSIDE_LIMIT")
            if case.lot_no in lots and len(lots) > 1:
                return True
            continue
        if case.lot_no in _signal_lots(case, chart, "I_EIGHT_SAME_SIDE"):
            return True
        if finding.cause == "EV_DRIFT":
            lots = _signal_lots(case, chart, "I_OUTSIDE_3SIGMA")
            if case.lot_no in lots and len(lots) > 1:
                return True
    return False


def score(case: Case, finding: Finding, evidence: dict[str, Evidence]) -> Score:
    cited = set(finding.evidence_ids)
    missing = cited - evidence.keys()
    references = [evidence[key] for key in cited if key in evidence]
    supported = bool(references) and not missing and _supports(case, finding, references)
    root_hit = (finding.cause, finding.entity) == (case.cause, case.entity)
    return Score(
        root_hit=root_hit,
        grounded_hit=root_hit and supported,
        invalid_citations=len(missing),
        unsupported_conclusion=finding.cause != "UNKNOWN" and not supported,
    )
