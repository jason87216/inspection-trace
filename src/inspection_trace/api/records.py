"""Filter and page persisted inspection measurements."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from inspection_trace.db import get_session
from inspection_trace.models import ImportBatch, InspectionItem, InspectionRecord

router = APIRouter(prefix="/records", tags=["records"])
type RecordFilter = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)
]


class RecordView(BaseModel):
    id: int
    lot_no: str
    station_code: str
    item_code: str
    item_name: str
    panel: int
    point: int | None
    machine_code: str
    measured_at: datetime
    operator: str
    value: float
    unit: str
    lsl: float | None
    target: float | None
    usl: float
    out_of_spec: bool
    import_batch_id: int
    source_row_no: int
    file_name: str


class RecordPage(BaseModel):
    items: list[RecordView]
    page: int
    page_size: int
    total: int


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
) -> RecordPage:
    filters = [
        column == value
        for column, value in (
            (InspectionRecord.lot_no, lot_no),
            (InspectionRecord.station_code, station_code),
            (InspectionRecord.machine_code, machine_code),
            (InspectionRecord.item_code, item_code),
        )
        if value is not None
    ]
    if start and end and start > end:
        raise HTTPException(422, "起始時間不可晚於結束時間")
    if start:
        filters.append(InspectionRecord.measured_at >= start)
    if end:
        filters.append(InspectionRecord.measured_at <= end)
    if not filters:
        raise HTTPException(422, "至少輸入一個查詢條件")

    violation = or_(
        InspectionRecord.value > InspectionItem.usl, InspectionRecord.value < InspectionItem.lsl
    )
    rows = (
        select(
            InspectionRecord.id,
            InspectionRecord.lot_no,
            InspectionRecord.station_code,
            InspectionRecord.item_code,
            InspectionItem.name.label("item_name"),
            InspectionRecord.panel,
            InspectionRecord.point,
            InspectionRecord.machine_code,
            InspectionRecord.measured_at,
            InspectionRecord.operator,
            InspectionRecord.value,
            InspectionItem.unit,
            InspectionItem.lsl,
            InspectionItem.target,
            InspectionItem.usl,
            func.coalesce(violation, False).label("out_of_spec"),
            InspectionRecord.import_batch_id,
            InspectionRecord.source_row_no,
            ImportBatch.file_name,
        )
        .join(InspectionItem, InspectionItem.code == InspectionRecord.item_code)
        .join(ImportBatch, ImportBatch.id == InspectionRecord.import_batch_id)
        .where(*filters)
    )
    if out_of_spec:
        rows = rows.where(violation)
    count = session.scalar(select(func.count()).select_from(rows.subquery())) or 0
    results = session.execute(
        rows.order_by(InspectionRecord.measured_at.desc(), InspectionRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    return RecordPage(
        items=[RecordView.model_validate(row) for row in results],
        page=page,
        page_size=page_size,
        total=count,
    )
