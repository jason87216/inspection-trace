"""Bound raw-file uploads consistently across import and preview endpoints."""

from pathlib import PurePath

from fastapi import HTTPException, Request
from starlette.requests import ClientDisconnect

from inspection_trace.ingestion.readers import MAX_FILE_BYTES


async def read_upload(request: Request, file_name: str, extensions: set[str]) -> bytes:
    if (
        request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
        != "application/octet-stream"
    ):
        raise HTTPException(415, "請以 application/octet-stream 傳送檔案")
    if (
        any(character in file_name for character in "/\\:")
        or any(ord(character) < 32 for character in file_name)
        or PurePath(file_name).suffix.lower() not in extensions
    ):
        raise HTTPException(422, "檔名或副檔名不符合支援格式")
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
    return bytes(data)
