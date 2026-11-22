"""HTTP interface for records queries."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, StringConstraints
from sqlalchemy.orm import Session

from inspection_trace.db import get_session
from inspection_trace.services import records as queries

router = APIRouter(prefix="/records", tags=["records"])
type RecordFilter = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)
]


@router.get("")
def records(
    session: Annotated[Session, Depends(get_session)],
    lot_no: RecordFilter | None = None,
    station_code: RecordFilter | None = None,
    machine_code: RecordFilter | None = None,
    item_code: RecordFilter | None = None,
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    out_of_spec: bool = False,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> queries.RecordPage:
    return queries.records(
        session,
        lot_no=lot_no,
        station_code=station_code,
        machine_code=machine_code,
        item_code=item_code,
        start=start,
        end=end,
        out_of_spec=out_of_spec,
        page=page,
        page_size=page_size,
    )
