"""GET /health: report app status and check the database with a real query."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from inspection_trace.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health(session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    """Run SELECT 1 on the database: 200 if it answers, 503 if it does not."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # Details go to the log, not to the client.
        logger.warning("health check: database query failed", exc_info=True)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})
