"""Batch details, lineage and process waiting-time queries."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from pydantic import BaseModel
from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import CTE

from inspection_trace.models import Lot, LotLink, LotMove, QTimeRule, Station
from inspection_trace.services.errors import QueryError, require_aware_times
from inspection_trace.trace.lineage import TraceDirection, TraceLink, trace_lot
from inspection_trace.trace.qtime import QTimeStatus, calculate_qtime

SPLIT_DEPTH_LIMIT = 10


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


class _RecordedWait(NamedTuple):
    recorded_at: datetime
    rule_id: int
    result: QTimeView


def _require_lot(session: Session, lot_no: str) -> Lot:
    lot = session.get(Lot, lot_no)
    if lot is None:
        raise QueryError(404, "找不到這個批號")
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


def _split_paths(lot_numbers: Select[tuple[str]], direction: TraceDirection) -> CTE:
    """Expand each starting lot through split edges, retaining its distance from the start."""
    seeds = lot_numbers.subquery()
    paths = select(
        seeds.c.lot_no, seeds.c.lot_no.label("related_lot_no"), literal(0).label("depth")
    ).cte(f"qtime_{direction}", recursive=True)
    if direction == "up":
        current, related = LotLink.child_lot_no, LotLink.parent_lot_no
    else:
        current, related = LotLink.parent_lot_no, LotLink.child_lot_no

    # UNION collapses equal-length paths to the same lot. The depth cap bounds cycles.
    return paths.union(
        select(paths.c.lot_no, related, paths.c.depth + 1)
        .select_from(paths)
        .join(LotLink, and_(current == paths.c.related_lot_no, LotLink.relation == "split"))
        .where(paths.c.depth < SPLIT_DEPTH_LIMIT)
    )


def _resolved_waits(
    session: Session, lot_numbers: Select[tuple[str]], rules: list[QTimeRule]
) -> Iterator[tuple[str, QTimeRule, LotMove | None, LotMove | None]]:
    """Resolve each station from the lot itself or its unique nearest split ancestor."""
    if not rules:
        return
    ancestors = _split_paths(lot_numbers, "up")
    stations = {
        station for rule in rules for station in (rule.from_station_code, rule.to_station_code)
    }
    ranked = (
        select(
            ancestors.c.lot_no,
            LotMove.id.label("move_id"),
            func.dense_rank()
            .over(
                partition_by=(ancestors.c.lot_no, LotMove.station_code),
                order_by=ancestors.c.depth,
            )
            .label("proximity"),
        )
        .join(LotMove, LotMove.lot_no == ancestors.c.related_lot_no)
        .where(LotMove.station_code.in_(stations))
        .subquery()
    )
    nearest = (
        select(ranked.c.lot_no, LotMove)
        .join(LotMove, LotMove.id == ranked.c.move_id)
        .where(ranked.c.proximity == 1)
    )
    by_lot: dict[str, dict[str, LotMove | None]] = {}
    for lot_no, move in session.execute(nearest):
        movements = by_lot.setdefault(lot_no, {})
        movements[move.station_code] = None if move.station_code in movements else move

    for lot_no, movements in by_lot.items():
        for rule in rules:
            upstream = movements.get(rule.from_station_code)
            downstream = movements.get(rule.to_station_code)
            if upstream is not None or downstream is not None:
                yield lot_no, rule, upstream, downstream


def lot_detail(lot_no: str, session: Session) -> LotView:
    lot = _require_lot(session, lot_no)
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
    qtime = []
    if lot.lot_type == "production":
        rules = list(session.scalars(select(QTimeRule).order_by(QTimeRule.id)))
        lot_numbers = select(Lot.lot_no).where(Lot.lot_no == lot_no)
        qtime = [_check_wait(*wait) for wait in _resolved_waits(session, lot_numbers, rules)]
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


def lot_trace(
    lot_no: str,
    session: Session,
    direction: TraceDirection = "up",
    max_depth: int = 10,
) -> TraceView:
    if direction not in ("up", "down") or not 1 <= max_depth <= 10:
        raise QueryError(422, "追溯方向須為 up 或 down，深度須介於 1 至 10")
    _require_lot(session, lot_no)
    return TraceView(
        lot_no=lot_no,
        direction=direction,
        max_depth=max_depth,
        links=trace_lot(session, lot_no, direction, max_depth=max_depth),
    )


def _recorded_at(upstream: LotMove | None, downstream: LotMove | None) -> datetime:
    if downstream is not None:
        return downstream.start_at.astimezone(UTC)
    assert upstream is not None
    return (upstream.end_at or upstream.start_at).astimezone(UTC)


def _wait_owner_order(wait: QTimeView) -> tuple[bool, bool, str]:
    """Prefer the lot that recorded the arrival, or the departure while pending."""
    return wait.lot_no != wait.to_lot_no, wait.lot_no != wait.from_lot_no, wait.lot_no


def period_qtime(
    session: Session,
    start: datetime,
    end: datetime,
    over_only: bool = False,
    *,
    limit: int | None = None,
) -> QTimePeriodView:
    """Filter actual recorded waits by downstream arrival, or upstream time while pending."""
    require_aware_times(start, end)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if not timedelta(0) < end - start <= timedelta(days=31):
        raise QueryError(422, "查詢期間須大於 0 且不超過 31 天")

    rules = list(session.scalars(select(QTimeRule).order_by(QTimeRule.id)))
    arrival_in_period = and_(
        LotMove.station_code.in_({rule.to_station_code for rule in rules}),
        LotMove.start_at >= start,
        LotMove.start_at < end,
    )
    event_in_period = arrival_in_period
    if not over_only:
        departure_at = func.coalesce(LotMove.end_at, LotMove.start_at)
        event_in_period = or_(
            arrival_in_period,
            and_(
                LotMove.station_code.in_({rule.from_station_code for rule in rules}),
                departure_at >= start,
                departure_at < end,
            ),
        )
    sources = select(LotMove.lot_no).where(event_in_period).distinct()
    descendants = _split_paths(sources, "down")
    lot_numbers = (
        select(Lot.lot_no)
        .join(descendants, descendants.c.related_lot_no == Lot.lot_no)
        .where(Lot.lot_type == "production")
        .distinct()
    )

    # Final filtering uses resolved times. The other end of a wait may fall outside the period.
    unique: dict[tuple[int, int | None, int | None], _RecordedWait] = {}
    for lot_no, rule, upstream, downstream in _resolved_waits(session, lot_numbers, rules):
        recorded_at = _recorded_at(upstream, downstream)
        if not start <= recorded_at < end:
            continue
        wait = _check_wait(lot_no, rule, upstream, downstream)
        if over_only and wait.status != "over_limit":
            continue
        key = (rule.id, upstream.id if upstream else None, downstream.id if downstream else None)
        previous = unique.get(key)
        if previous is None or _wait_owner_order(wait) < _wait_owner_order(previous.result):
            unique[key] = _RecordedWait(recorded_at, rule.id, wait)

    ordered = sorted(unique.values(), key=lambda item: (item.result.lot_no, item.rule_id))
    ordered.sort(key=lambda item: item.recorded_at, reverse=True)
    return QTimePeriodView(items=[item.result for item in ordered[:limit]])
