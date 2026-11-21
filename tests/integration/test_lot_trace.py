"""Acceptance cases for the P5 lineage query."""

import pytest
from sqlalchemy.orm import Session

from inspection_trace.models import LotLink
from inspection_trace.trace.lineage import TraceLink, trace_lot

pytestmark = pytest.mark.integration


def test_trace_up_finds_both_material_lots(seeded_session: Session) -> None:
    links = trace_lot(seeded_session, "L261102A001", "up")

    assert set(links) == {
        TraceLink("ABF-3619", "L261102A001", "consume", 1),
        TraceLink("CORE-5456", "L261102A001", "consume", 1),
    }


def test_trace_down_reaches_split_child_at_depth_two(seeded_session: Session) -> None:
    seeded_session.add(
        LotLink(parent_lot_no="L261102A001", child_lot_no="L261102C007", relation="split")
    )
    seeded_session.flush()

    split = TraceLink("L261102A001", "L261102C007", "split", 2)
    assert split not in trace_lot(seeded_session, "ABF-3619", "down", max_depth=1)
    assert split in trace_lot(seeded_session, "ABF-3619", "down", max_depth=2)


def test_trace_down_ignores_the_edge_back_into_current_path(seeded_session: Session) -> None:
    first, second = "L261102A001", "L261102C007"
    seeded_session.add_all(
        [
            LotLink(parent_lot_no=first, child_lot_no=second, relation="split"),
            LotLink(parent_lot_no=second, child_lot_no=first, relation="split"),
        ]
    )
    seeded_session.flush()

    assert trace_lot(seeded_session, first, "down") == [TraceLink(first, second, "split", 1)]


def test_trace_reports_the_shortest_depth_when_two_paths_reach_an_edge(
    seeded_session: Session,
) -> None:
    first, middle, last = "L261102A001", "L261102B002", "L261102C007"
    seeded_session.add_all(
        [
            LotLink(parent_lot_no=first, child_lot_no=middle, relation="split"),
            LotLink(parent_lot_no=middle, child_lot_no=last, relation="split"),
        ]
    )
    seeded_session.flush()

    links = trace_lot(seeded_session, "ABF-3619", "down", max_depth=3)

    assert [link for link in links if link.child_lot_no == last] == [
        TraceLink(middle, last, "split", 2)
    ]
