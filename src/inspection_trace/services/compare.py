"""Summarize a single inspection item by machine, material, or Taipei shift."""

from datetime import UTC, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Time, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from inspection_trace.models import InspectionItem, InspectionRecord, LotLink
from inspection_trace.services.errors import QueryError, require_aware_times

type GroupBy = Literal["machine", "material", "shift"]


class GroupSummary(BaseModel):
    group: str
    count: int
    mean: float
    out_of_spec_count: int
    out_of_spec_rate: float
    evidence_record_id: int


def compare_groups(
    session: Session,
    item_code: str,
    start: datetime,
    end: datetime,
    group_by: GroupBy,
) -> list[GroupSummary]:
    require_aware_times(start, end)
    if not timedelta(0) < end.astimezone(UTC) - start.astimezone(UTC) <= timedelta(days=31):
        raise QueryError(422, "查詢期間須大於 0 且不超過 31 天")
    item = session.get(InspectionItem, item_code)
    if item is None:
        raise QueryError(404, "找不到檢驗項目")

    record = InspectionRecord
    filters = (record.item_code == item_code, record.measured_at >= start, record.measured_at < end)
    joins = None
    if group_by == "machine":
        group = record.machine_code
    elif group_by == "shift":
        taipei_time = cast(func.timezone("Asia/Taipei", record.measured_at), Time)
        group = case((and_(taipei_time >= time(8), taipei_time < time(20)), "day"), else_="night")
    elif group_by == "material":
        ancestors = (
            select(record.lot_no.label("lot"), record.lot_no.label("ancestor"))
            .where(*filters)
            .distinct()
            .cte("ancestors", recursive=True)
        )
        split = aliased(LotLink)
        ancestors = ancestors.union(
            select(ancestors.c.lot, split.parent_lot_no).join(
                split, and_(split.child_lot_no == ancestors.c.ancestor, split.relation == "split")
            )
        )
        material = (
            select(ancestors.c.lot, LotLink.parent_lot_no.label("material_lot"))
            .join(
                LotLink,
                and_(LotLink.child_lot_no == ancestors.c.ancestor, LotLink.relation == "consume"),
            )
            .distinct()
            .subquery()
        )
        group = material.c.material_lot
        joins = material
    else:
        raise QueryError(422, "未知的分組欄位")

    out_of_spec = record.value > item.usl
    if item.lsl is not None:
        out_of_spec = or_(out_of_spec, record.value < item.lsl)
    query = select(
        group,
        func.count(),
        func.avg(record.value),
        func.sum(case((out_of_spec, 1), else_=0)),
        func.min(record.id),
    ).select_from(record)
    if joins is not None:
        query = query.join(joins, joins.c.lot == record.lot_no)
    rows = session.execute(query.where(*filters).group_by(group).order_by(group).limit(101)).all()
    if len(rows) > 100:
        raise QueryError(422, "分組超過 100 組，請縮小查詢期間")
    return [
        GroupSummary(
            group=key,
            count=count,
            mean=mean,
            out_of_spec_count=bad,
            out_of_spec_rate=bad / count,
            evidence_record_id=record_id,
        )
        for key, count, mean, bad, record_id in rows
    ]
