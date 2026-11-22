from datetime import datetime
from typing import cast

import pytest
from sqlalchemy.orm import Session

from inspection_trace.services.errors import QueryError
from inspection_trace.services.lots import lot_trace, period_qtime
from inspection_trace.services.records import records
from inspection_trace.services.spc import chart


def test_service_validates_bounds_without_an_http_request_or_database() -> None:
    session = cast(Session, None)
    unzoned = datetime(2026, 11, 2)
    queries = (
        lambda: lot_trace("L261102A001", session, max_depth=11),
        lambda: period_qtime(session, unzoned, unzoned),
        lambda: records(session, item_code="CORE_LW", page_size=101),
        lambda: chart(session, "CORE_LW", "LIN-01", baseline_points=201),
    )
    for query in queries:
        with pytest.raises(QueryError) as caught:
            query()
        assert caught.value.status_code == 422
