"""HTTP endpoints for lot history, lineage, and waiting times."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from inspection_trace.db import get_session
from inspection_trace.services import lots
from inspection_trace.trace.lineage import TraceDirection

router = APIRouter(tags=["trace"])
type DbSession = Annotated[Session, Depends(get_session)]
type LotCode = Annotated[str, Path(min_length=1, max_length=30)]


@router.get("/lots/{lot_no}")
def lot_detail(lot_no: LotCode, session: DbSession) -> lots.LotView:
    return lots.lot_detail(lot_no, session)


@router.get("/lots/{lot_no}/trace")
def lot_trace(
    lot_no: LotCode,
    session: DbSession,
    direction: TraceDirection = "up",
    max_depth: Annotated[int, Query(ge=1, le=10)] = 10,
) -> lots.TraceView:
    return lots.lot_trace(lot_no, session, direction, max_depth)


@router.get("/qtime")
def period_qtime(
    session: DbSession,
    start: Annotated[AwareDatetime, Query(alias="from")],
    end: Annotated[AwareDatetime, Query(alias="to")],
    over_only: bool = False,
) -> lots.QTimePeriodView:
    return lots.period_qtime(session, start, end, over_only)
