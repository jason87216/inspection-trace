"""Upload, history, and downloadable import errors."""

from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from inspection_trace.db import get_session
from inspection_trace.ingestion.loader import import_file
from inspection_trace.ingestion.readers import MAX_FILE_BYTES
from inspection_trace.ingestion.report import REPORT_MEDIA_TYPE, build_error_report
from inspection_trace.models import ImportBatch
from inspection_trace.services import imports as queries

router = APIRouter(prefix="/imports", tags=["imports"])
type DbSession = Annotated[Session, Depends(get_session)]


@router.get("")
def history(
    session: DbSession,
    status: Literal["ACCEPT", "REJECT", "DUPLICATE_FILE"] | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> queries.ImportPage:
    return queries.history(session, status, page, page_size)


@router.get("/{batch_id}")
def get_import(batch_id: int, session: DbSession) -> queries.ImportDetail:
    return queries.get_import(batch_id, session)


def _submit(session: Session, file_name: str, data: bytes) -> queries.ImportDetail:
    batch_id = import_file(session, file_name, data)
    return queries.get_import(batch_id, session)


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
) -> queries.ImportDetail:
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
    errors = list(session.scalars(queries.import_error_rows(batch.duplicate_of_id or batch.id)))
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
