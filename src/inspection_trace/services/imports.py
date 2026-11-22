"""Read import history and deterministic error details."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select

from inspection_trace.models import ImportBatch, ImportErrorDetail, InspectionRecord
from inspection_trace.services.errors import QueryError


class ImportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    source_type: str
    station_code: str | None
    status: str
    total_rows: int
    error_rows: int
    created_at: datetime
    duplicate_of_id: int | None
    duplicate_file_name: str | None = None
    warnings: list[str]
    inserted_count: int = 0
    error_count: int = 0


class ErrorView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_no: int | None
    column: str | None
    code: str
    message: str
    raw_value: str | None


class ImportDetail(ImportSummary):
    errors: list[ErrorView]


class ImportPage(BaseModel):
    items: list[ImportSummary]
    page: int
    page_size: int
    total: int


def _summaries() -> Select[tuple[ImportBatch, int, int, str | None]]:
    original = aliased(ImportBatch)
    records = (
        select(func.count(InspectionRecord.id))
        .where(InspectionRecord.import_batch_id == ImportBatch.id)
        .scalar_subquery()
    )
    errors = (
        select(func.count(ImportErrorDetail.id))
        .where(ImportErrorDetail.import_batch_id == ImportBatch.id)
        .scalar_subquery()
    )
    return select(ImportBatch, records, errors, original.file_name).outerjoin(
        original, original.id == ImportBatch.duplicate_of_id
    )


def _summary(batch: ImportBatch, count: int, errors: int, original: str | None) -> ImportSummary:
    return ImportSummary.model_validate(batch).model_copy(
        update={"inserted_count": count, "error_count": errors, "duplicate_file_name": original}
    )


def import_error_rows(batch_id: int) -> Select[tuple[ImportErrorDetail]]:
    return (
        select(ImportErrorDetail)
        .where(ImportErrorDetail.import_batch_id == batch_id)
        .order_by(ImportErrorDetail.row_no.asc().nulls_first(), ImportErrorDetail.id)
    )


def history(
    session: Session,
    status: Literal["ACCEPT", "REJECT", "DUPLICATE_FILE"] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ImportPage:
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise QueryError(422, "分頁或每頁筆數超出查詢限制")
    rows = _summaries()
    count = select(func.count()).select_from(ImportBatch)
    if status:
        rows = rows.where(ImportBatch.status == status)
        count = count.where(ImportBatch.status == status)
    result = session.execute(
        rows.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ImportPage(
        items=[_summary(*row) for row in result],
        page=page,
        page_size=page_size,
        total=session.scalar(count) or 0,
    )


def get_import(batch_id: int, session: Session) -> ImportDetail:
    result = session.execute(_summaries().where(ImportBatch.id == batch_id)).one_or_none()
    if result is None:
        raise QueryError(404, "找不到這筆匯入")
    return ImportDetail(
        **_summary(*result).model_dump(),
        errors=[
            ErrorView.model_validate(error)
            for error in session.scalars(import_error_rows(batch_id).limit(100))
        ],
    )
