"""Validation errors shared by the REST and MCP adapters."""

from datetime import datetime
from typing import Literal


class QueryError(ValueError):
    def __init__(self, status_code: Literal[404, 422], detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def require_aware_times(*times: datetime | None) -> None:
    if any(value is not None and value.utcoffset() is None for value in times):
        raise QueryError(422, "查詢時間須包含時區")
