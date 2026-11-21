"""Upload, history, and downloadable import errors."""

from datetime import datetime
from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select
from starlette.requests import ClientDisconnect

from inspection_trace.db import get_session
from inspection_trace.ingestion.loader import import_file
from inspection_trace.ingestion.readers import MAX_FILE_BYTES
from inspection_trace.ingestion.report import REPORT_MEDIA_TYPE, build_error_report
from inspection_trace.models import ImportBatch, ImportErrorDetail, InspectionRecord

router = APIRouter(prefix="/imports", tags=["imports"])
type DbSession = Annotated[Session, Depends(get_session)]


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


def _errors(batch_id: int) -> Select[tuple[ImportErrorDetail]]:
    return (
        select(ImportErrorDetail)
        .where(ImportErrorDetail.import_batch_id == batch_id)
        .order_by(ImportErrorDetail.row_no.asc().nulls_first(), ImportErrorDetail.id)
    )


@router.get("")
def history(
    session: DbSession,
    status: Literal["ACCEPT", "REJECT", "DUPLICATE_FILE"] | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportPage:
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


@router.get("/{batch_id}")
def get_import(batch_id: int, session: DbSession) -> ImportDetail:
    result = session.execute(_summaries().where(ImportBatch.id == batch_id)).one_or_none()
    if result is None:
        raise HTTPException(404, "找不到這筆匯入")
    return ImportDetail(
        **_summary(*result).model_dump(),
        errors=[
            ErrorView.model_validate(error)
            for error in session.scalars(_errors(batch_id).limit(100))
        ],
    )


def _submit(session: Session, file_name: str, data: bytes) -> ImportDetail:
    batch_id = import_file(session, file_name, data)
    return get_import(batch_id, session)


@router.post(
    "",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    request: Request,
    session: DbSession,
    file_name: Annotated[str, Query(min_length=1, max_length=200)],
) -> ImportDetail:
    """Accept one raw CSV/XLSX payload and return the saved attempt."""
    if (
        request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        != "application/octet-stream"
    ):
        raise HTTPException(415, "請以 application/octet-stream 傳送檔案")
    if (
        any(character in file_name for character in "/\\:")
        or any(ord(character) < 32 for character in file_name)
        or PurePath(file_name).suffix.lower() not in {".csv", ".xlsx"}
    ):
        raise HTTPException(422, "請提供有效的 CSV 或 XLSX 檔名")
    data = bytearray()
    try:
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, "單一檔案上限為 5 MB")
            data.extend(chunk)
    except ClientDisconnect as exc:
        raise HTTPException(400, "檔案傳輸中斷") from exc
    if not data:
        raise HTTPException(422, "檔案是空的")
    return await run_in_threadpool(_submit, session, file_name, bytes(data))


@router.get("/{batch_id}/error-report")
def download_report(batch_id: int, session: DbSession) -> Response:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "找不到這筆匯入")
    errors = list(session.scalars(_errors(batch.duplicate_of_id or batch.id)))
    if not errors:
        raise HTTPException(404, "這筆匯入沒有錯誤報告")
    return Response(
        build_error_report(errors),
        media_type=REPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="import-{batch_id}-errors.xlsx"',
            "X-Content-Type-Options": "nosniff",
        },
    )
