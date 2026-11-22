"""Filter and page persisted inspection measurements."""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from inspection_trace.models import ImportBatch, InspectionItem, InspectionRecord
from inspection_trace.services.errors import QueryError, require_aware_times


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


def records(
    session: Session,
    lot_no: str | None = None,
    station_code: str | None = None,
    machine_code: str | None = None,
    item_code: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    out_of_spec: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> RecordPage:
    require_aware_times(start, end)
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise QueryError(422, "分頁或每頁筆數超出查詢限制")
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
        raise QueryError(422, "起始時間不可晚於結束時間")
    if start:
        filters.append(InspectionRecord.measured_at >= start)
    if end:
        filters.append(InspectionRecord.measured_at <= end)
    if not filters:
        raise QueryError(422, "至少輸入一個查詢條件")

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
