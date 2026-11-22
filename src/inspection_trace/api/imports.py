"""Upload, history, and downloadable import errors."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from inspection_trace.api.upload import read_upload
from inspection_trace.db import get_session
from inspection_trace.ingestion.loader import UnapprovedTemplate, import_file
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


def _submit(
    session: Session, file_name: str, data: bytes, template_id: int | None
) -> queries.ImportDetail:
    try:
        batch_id = import_file(session, file_name, data, template_id=template_id)
    except UnapprovedTemplate as exc:
        raise HTTPException(409, str(exc)) from exc
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
    template_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
) -> queries.ImportDetail:
    """Accept one raw CSV/XLSX/PDF payload and return the saved attempt."""
    if template_id is not None and file_name.lower().endswith(".pdf"):
        raise HTTPException(422, "固定版面 PDF 請使用自動辨識")
    data = await read_upload(request, file_name, {".csv", ".xlsx", ".pdf"})
    return await run_in_threadpool(_submit, session, file_name, data, template_id)


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
