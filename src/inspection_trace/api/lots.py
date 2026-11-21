"""Batch details, lineage and process waiting-time queries."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session, aliased

from inspection_trace.db import get_session
from inspection_trace.models import Lot, LotMove, QTimeRule, Station
from inspection_trace.trace.lineage import TraceDirection, TraceLink, trace_lot
from inspection_trace.trace.qtime import QTimeStatus, calculate_qtime

router = APIRouter(tags=["trace"])
type DbSession = Annotated[Session, Depends(get_session)]
type LotCode = Annotated[str, Path(min_length=1, max_length=30)]


class MovementView(BaseModel):
    station_code: str
    station_name: str
    machine_code: str
    start_at: datetime
    end_at: datetime | None
    operator: str


class QTimeView(BaseModel):
    lot_no: str
    from_station_code: str
    to_station_code: str
    from_lot_no: str | None
    to_lot_no: str | None
    from_end: datetime | None
    to_start: datetime | None
    limit_hours: float
    status: QTimeStatus
    hours: float | None


class LotView(BaseModel):
    lot_no: str
    lot_type: str
    part_no: str | None
    panel_count: int | None
    released_at: datetime | None
    kind: str | None
    movements: list[MovementView]
    qtime: list[QTimeView]


class TraceView(BaseModel):
    lot_no: str
    direction: TraceDirection
    max_depth: int
    links: list[TraceLink]


class QTimePeriodView(BaseModel):
    items: list[QTimeView]


def _lot_or_404(session: Session, lot_no: str) -> Lot:
    lot = session.get(Lot, lot_no)
    if lot is None:
        raise HTTPException(404, "找不到這個批號")
    return lot


def _check_wait(
    lot_no: str, rule: QTimeRule, upstream: LotMove | None, downstream: LotMove | None
) -> QTimeView:
    from_end = upstream.end_at if upstream else None
    to_start = downstream.start_at if downstream else None
    result = calculate_qtime(from_end, to_start, rule.limit_hours)
    return QTimeView(
        lot_no=lot_no,
        from_station_code=rule.from_station_code,
        to_station_code=rule.to_station_code,
        from_lot_no=upstream.lot_no if upstream else None,
        to_lot_no=downstream.lot_no if downstream else None,
        from_end=from_end,
        to_start=to_start,
        limit_hours=rule.limit_hours,
        status=result.status,
        hours=result.hours,
    )


def _waits_for_lot(session: Session, lot: Lot, movements: dict[str, LotMove]) -> list[QTimeView]:
    if lot.lot_type != "production":
        return []
    rules = list(session.scalars(select(QTimeRule).order_by(QTimeRule.id)))
    stations = {
        station for rule in rules for station in (rule.from_station_code, rule.to_station_code)
    }
    missing = stations - movements.keys()
    if missing:
        # After a split, the child can inherit earlier station evidence from the parent.
        parent_depths: dict[str, int] = {}
        for edge in trace_lot(session, lot.lot_no, "up"):
            if edge.relation == "split":
                parent_depths.setdefault(edge.parent_lot_no, edge.depth)
        if parent_depths:
            candidates = list(
                session.scalars(
                    select(LotMove).where(
                        LotMove.lot_no.in_(parent_depths), LotMove.station_code.in_(missing)
                    )
                )
            )
            for station in missing:
                station_moves = [move for move in candidates if move.station_code == station]
                if not station_moves:
                    continue
                nearest = min(parent_depths[move.lot_no] for move in station_moves)
                closest = [move for move in station_moves if parent_depths[move.lot_no] == nearest]
                if len(closest) == 1:
                    movements[station] = closest[0]
    return [
        _check_wait(
            lot.lot_no,
            rule,
            movements.get(rule.from_station_code),
            movements.get(rule.to_station_code),
        )
        for rule in rules
        if rule.from_station_code in movements or rule.to_station_code in movements
    ]


@router.get("/lots/{lot_no}")
def lot_detail(lot_no: LotCode, session: DbSession) -> LotView:
    lot = _lot_or_404(session, lot_no)
    rows = list(
        session.execute(
            select(LotMove, Station.name)
            .join(Station, Station.code == LotMove.station_code)
            .where(LotMove.lot_no == lot_no)
            .order_by(LotMove.start_at, LotMove.id)
        )
    )
    movements = [
        MovementView(
            station_code=move.station_code,
            station_name=name,
            machine_code=move.machine_code,
            start_at=move.start_at,
            end_at=move.end_at,
            operator=move.operator,
        )
        for move, name in rows
    ]
    qtime = _waits_for_lot(session, lot, {move.station_code: move for move, _ in rows})
    return LotView(
        lot_no=lot.lot_no,
        lot_type=lot.lot_type,
        part_no=lot.part_no,
        panel_count=lot.panel_count,
        released_at=lot.released_at,
        kind=lot.kind,
        movements=movements,
        qtime=qtime,
    )


@router.get("/lots/{lot_no}/trace")
def lot_trace(
    lot_no: LotCode,
    session: DbSession,
    direction: TraceDirection = "up",
    max_depth: Annotated[int, Query(ge=1, le=10)] = 10,
) -> TraceView:
    _lot_or_404(session, lot_no)
    return TraceView(
        lot_no=lot_no,
        direction=direction,
        max_depth=max_depth,
        links=trace_lot(session, lot_no, direction, max_depth=max_depth),
    )


@router.get("/qtime")
def period_qtime(
    session: DbSession,
    start: Annotated[AwareDatetime, Query(alias="from")],
    end: Annotated[AwareDatetime, Query(alias="to")],
    over_only: bool = False,
) -> QTimePeriodView:
    """Filter actual recorded waits by downstream arrival, or upstream time while pending."""
    duration = end.astimezone(UTC) - start.astimezone(UTC)
    if not timedelta(0) < duration <= timedelta(days=31):
        raise HTTPException(422, "查詢期間須大於 0 且不超過 31 天")

    upstream, downstream = aliased(LotMove), aliased(LotMove)
    recorded_at = func.coalesce(downstream.start_at, upstream.end_at, upstream.start_at)
    rows = session.execute(
        select(Lot.lot_no, QTimeRule, upstream, downstream)
        .select_from(Lot)
        .join(QTimeRule, true())
        .outerjoin(
            upstream,
            and_(
                upstream.lot_no == Lot.lot_no, upstream.station_code == QTimeRule.from_station_code
            ),
        )
        .outerjoin(
            downstream,
            and_(
                downstream.lot_no == Lot.lot_no,
                downstream.station_code == QTimeRule.to_station_code,
            ),
        )
        .where(Lot.lot_type == "production", recorded_at >= start, recorded_at < end)
        .order_by(recorded_at.desc(), Lot.lot_no, QTimeRule.id)
    )
    items = [_check_wait(lot_no, rule, left, right) for lot_no, rule, left, right in rows]
    if over_only:
        items = [item for item in items if item.status == "over_limit"]
    return QTimePeriodView(items=items)
