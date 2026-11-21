"""Read an I-MR chart for one continuous item and machine."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import AwareDatetime, BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from inspection_trace.db import get_session
from inspection_trace.models import InspectionItem, InspectionRecord, Machine
from inspection_trace.spc.chart import Limits, Signal, build_chart

router = APIRouter(prefix="/spc", tags=["spc"])
MAX_CHART_POINTS = 1000


class Observation(BaseModel):
    id: int
    lot_no: str
    measured_at: datetime
    value: float
    moving_range: float | None
    signals: tuple[Signal, ...]


class ChartView(BaseModel):
    item_code: str
    machine_code: str
    unit: str
    baseline_points: int
    individual_limits: Limits | None
    range_limits: Limits | None
    points: list[Observation]


@router.get("/{item_code}")
def chart(
    session: Annotated[Session, Depends(get_session)],
    item_code: Annotated[str, Path(min_length=1, max_length=30)],
    machine: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)],
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    baseline_points: Annotated[int, Query(ge=5, le=200)] = 20,
) -> ChartView:
    """Use the first observations in the selected period as the fixed baseline."""
    if start and end and start > end:
        raise HTTPException(422, "起始時間不可晚於結束時間")
    item = session.get(InspectionItem, item_code)
    equipment = session.get(Machine, machine)
    if item is None or equipment is None:
        raise HTTPException(404, "找不到檢驗項目或機台")
    if item.kind != "continuous":
        raise HTTPException(422, "I-MR 管制圖適用於連續量測項目")
    if item.station_code != equipment.station_code:
        raise HTTPException(422, "檢驗項目與機台須屬於相同站點")

    rows = select(
        InspectionRecord.id,
        InspectionRecord.lot_no,
        InspectionRecord.measured_at,
        InspectionRecord.value,
    ).where(InspectionRecord.item_code == item_code, InspectionRecord.machine_code == machine)
    if start:
        rows = rows.where(InspectionRecord.measured_at >= start)
    if end:
        rows = rows.where(InspectionRecord.measured_at <= end)
    records = session.execute(
        rows.order_by(
            InspectionRecord.measured_at,
            InspectionRecord.lot_no,
            InspectionRecord.panel,
            InspectionRecord.point,
        ).limit(MAX_CHART_POINTS + 1)
    ).all()
    if len(records) > MAX_CHART_POINTS:
        raise HTTPException(422, f"量測超過 {MAX_CHART_POINTS} 筆，請縮小查詢期間")

    try:
        result = build_chart([record.value for record in records], baseline_points)
    except ValueError as error:
        raise HTTPException(422, "量測值超出管制圖可計算的範圍") from error
    return ChartView(
        item_code=item_code,
        machine_code=machine,
        unit=item.unit,
        baseline_points=baseline_points,
        individual_limits=result.individual_limits,
        range_limits=result.range_limits,
        points=[
            Observation(
                id=record.id,
                lot_no=record.lot_no,
                measured_at=record.measured_at,
                value=record.value,
                moving_range=point.moving_range,
                signals=point.signals,
            )
            for record, point in zip(records, result.points, strict=True)
        ],
    )
