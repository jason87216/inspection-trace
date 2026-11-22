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
            cases.append(Case(lot, label, entity))
            used[label] += 1
    return cases


def _objects(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _supports(cause: str, entity: str | None, evidence: Evidence) -> bool:
    if entity is None:
        return False
    for row in _objects(evidence.result):
        if cause == "EV_QTIME_OVER":
            if row.get("lot_no") == entity and row.get("status") == "over_limit":
                return True
        if cause == "EV_MAT_SHIFT":
            if (
                row.get("parent_lot_no") == entity
                and row.get("relation") == "consume"
                or row.get("group") == entity
                and row.get("evidence_record_id") is not None
            ):
                return True
        if cause in {"EV_DRIFT", "EV_BATH_AGING"}:
            if row.get("machine_code") == entity and (
                row.get("signal_count", 0) != 0 or row.get("value") is not None
            ):
                return True
            if row.get("group") == entity and row.get("evidence_record_id") is not None:
                return True
    return False


def score(case: Case, finding: Finding, evidence: dict[str, Evidence]) -> Score:
    cited = set(finding.evidence_ids)
    missing = cited - evidence.keys()
    references = [evidence[key] for key in cited if key in evidence]
    supported = (
        bool(references)
        and not missing
        and any(_supports(finding.cause, finding.entity, item) for item in references)
    )
    root_hit = (finding.cause, finding.entity) == (case.cause, case.entity)
    return Score(
        root_hit=root_hit,
        grounded_hit=root_hit and supported,
        invalid_citations=len(missing),
        unsupported_conclusion=finding.cause != "UNKNOWN" and not supported,
    )
