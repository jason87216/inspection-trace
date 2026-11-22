"""HTTP interface for spc queries."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, StringConstraints
from sqlalchemy.orm import Session

from inspection_trace.db import get_session
from inspection_trace.services import spc as queries

router = APIRouter(prefix="/spc", tags=["spc"])


@router.get("/{item_code}")
def chart(
    session: Annotated[Session, Depends(get_session)],
    item_code: Annotated[str, Path(min_length=1, max_length=30)],
    machine: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)],
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    baseline_points: Annotated[int, Query(ge=5, le=200)] = 20,
) -> queries.ChartView:
    return queries.chart(session, item_code, machine, start, end, baseline_points)
