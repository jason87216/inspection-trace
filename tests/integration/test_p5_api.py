"""Browser-facing batch history, trace and Q-time acceptance cases."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inspection_trace.models import Lot, LotLink, LotMove
from inspection_trace.services.lots import period_qtime

pytestmark = pytest.mark.integration
SAMPLE_LOT = "L261102A001"


def test_lot_detail_and_trace_show_the_same_batch_relationships(client: TestClient) -> None:
    response = client.get(f"/lots/{SAMPLE_LOT}")
    assert response.status_code == 200
    lot = response.json()
    assert (lot["lot_no"], lot["lot_type"], lot["part_no"]) == (
        SAMPLE_LOT,
        "production",
        "PN-ABF-1001",
    )
    assert [row["station_code"] for row in lot["movements"][:3]] == ["C10", "C20", "C30"]
    check = lot["qtime"][0]
    assert (check["status"], check["limit_hours"]) == ("within_limit", 8.0)
    assert check["hours"] == pytest.approx(3.73, abs=0.005)

    upstream = client.get(f"/lots/{SAMPLE_LOT}/trace", params={"direction": "up"}).json()
    assert {link["parent_lot_no"] for link in upstream["links"]} == {"ABF-3619", "CORE-5456"}
    downstream = client.get("/lots/ABF-3619/trace", params={"direction": "down"}).json()
    assert len(downstream["links"]) == 6
    assert all(link["parent_lot_no"] == "ABF-3619" for link in downstream["links"])
    assert client.get("/lots/MISSING").status_code == 404
    assert client.get("/lots/MISSING/trace").status_code == 404
    assert client.get(f"/lots/{SAMPLE_LOT}/trace", params={"max_depth": 11}).status_code == 422


def test_period_qtime_distinguishes_over_limit_and_missing_events(
    client: TestClient,
    seeded_session: Session,
) -> None:
    with seeded_session.begin():
        moves = {
            (move.lot_no, move.station_code): move
            for move in seeded_session.scalars(
                select(LotMove).where(
                    LotMove.lot_no.in_([SAMPLE_LOT, "L261102B002"]),
                    LotMove.station_code.in_(["B20", "B60"]),
                )
            )
        }
        early = moves[(SAMPLE_LOT, "B20")]
        late = moves[(SAMPLE_LOT, "B60")]
        late.start_at = early.end_at + timedelta(hours=9)
        late.end_at = late.start_at + timedelta(hours=1)
        moves[("L261102B002", "B20")].end_at = None

    params = {"from": "2026-11-02T00:00:00+08:00", "to": "2026-11-05T00:00:00+08:00"}
    response = client.get("/qtime", params=params)
    assert response.status_code == 200
    by_lot = {row["lot_no"]: row for row in response.json()["items"]}
    assert (by_lot[SAMPLE_LOT]["status"], by_lot[SAMPLE_LOT]["hours"]) == ("over_limit", 9.0)
    assert (by_lot["L261102B002"]["status"], by_lot["L261102B002"]["hours"]) == ("incomplete", None)
    only_over = client.get("/qtime", params={**params, "over_only": True}).json()["items"]
    assert [row["lot_no"] for row in only_over] == [SAMPLE_LOT]
    assert client.get("/qtime", params={**params, "to": params["from"]}).status_code == 422
    assert (
        client.get("/qtime", params={**params, "to": "2026-12-05T00:00:00+08:00"}).status_code
        == 422
    )


def test_split_lot_preserves_provenance_of_inherited_qtime(
    client: TestClient,
    seeded_session: Session,
) -> None:
    child = SAMPLE_LOT + "-S1"
    with seeded_session.begin():
        parent = seeded_session.get_one(Lot, SAMPLE_LOT)
        seeded_session.add(
            Lot(
                lot_no=child,
                lot_type="production",
                part_no=parent.part_no,
                panel_count=5,
                released_at=parent.released_at,
            )
        )
        seeded_session.flush()
        seeded_session.add(LotLink(parent_lot_no=SAMPLE_LOT, child_lot_no=child, relation="split"))

    response = client.get(f"/lots/{child}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["movements"] == []
    check = detail["qtime"][0]
    assert (check["lot_no"], check["from_lot_no"], check["to_lot_no"]) == (
        child,
        SAMPLE_LOT,
        SAMPLE_LOT,
    )
    arrived_at = datetime.fromisoformat(check["to_start"])
    params = {"from": arrived_at.isoformat(), "to": (arrived_at + timedelta(seconds=1)).isoformat()}
    items = client.get("/qtime", params=params).json()["items"]
    assert [row["lot_no"] for row in items] == [SAMPLE_LOT]


def test_period_qtime_counts_each_distinct_child_arrival(
    client: TestClient, seeded_session: Session
) -> None:
    parent, first, second, grandchild, within = (
        "L300102Z001",
        "L300102Z001-S1",
        "L300102Z001-S2",
        "L300102Z001-S1-A",
        "L300102Z001-S3",
    )
    arrival = datetime(2030, 1, 2, 12, tzinfo=UTC)
    with seeded_session.begin():
        seeded_session.add_all(
            Lot(
                lot_no=lot_no,
                lot_type="production",
                part_no="PN-ABF-1001",
                panel_count=5,
                released_at=arrival - timedelta(days=1),
            )
            for lot_no in (parent, first, second, grandchild, within)
        )
        seeded_session.flush()
        seeded_session.add_all(
            LotLink(parent_lot_no=source, child_lot_no=child, relation="split")
            for source, child in ((parent, first), (parent, second), (first, grandchild))
        )
        b20, b60 = seeded_session.scalars(
            select(LotMove)
            .where(LotMove.lot_no == SAMPLE_LOT, LotMove.station_code.in_(["B20", "B60"]))
            .order_by(LotMove.station_code)
        ).all()
        departure = arrival - timedelta(hours=9)
        seeded_session.add_all(
            LotMove(
                lot_no=lot_no,
                station_code=sample.station_code,
                machine_code=sample.machine_code,
                start_at=start_at,
                end_at=start_at + timedelta(hours=1),
                operator=sample.operator,
            )
            for lot_no, sample, start_at in (
                (parent, b20, departure - timedelta(hours=1)),
                (first, b60, arrival),
                (second, b60, arrival),
                (within, b20, arrival - timedelta(hours=2)),
                (within, b60, arrival + timedelta(hours=1)),
            )
        )

    first_check = client.get(f"/lots/{first}").json()["qtime"][0]
    second_check = client.get(f"/lots/{second}").json()["qtime"][0]
    assert (first_check["from_lot_no"], first_check["to_lot_no"], first_check["hours"]) == (
        parent,
        first,
        9.0,
    )
    assert first_check["status"] == "over_limit"
    assert client.get(f"/lots/{grandchild}").json()["qtime"][0] == {
        **first_check,
        "lot_no": grandchild,
    }

    end = arrival + timedelta(hours=2)
    params = {"from": arrival.isoformat(), "to": end.isoformat()}
    items = client.get("/qtime", params=params).json()["items"]
    assert [row["lot_no"] for row in items] == [within, first, second]
    assert items[1:] == [first_check, second_check]
    only_over = client.get("/qtime", params={**params, "over_only": True}).json()["items"]
    assert only_over == [first_check, second_check]
    limited = period_qtime(seeded_session, arrival, end, over_only=True, limit=1)
    assert [row.lot_no for row in limited.items] == [first]

    before = client.get("/qtime", params={"from": departure.isoformat(), "to": params["from"]})
    assert [(row["lot_no"], row["status"]) for row in before.json()["items"]] == [
        (parent, "incomplete")
    ]


def test_qtime_uses_only_the_unique_nearest_split_movement(
    client: TestClient, seeded_session: Session
) -> None:
    other_parent, bridge, child = "L261102B002", "L300103Z001", "L300103Z001-S1"
    arrival = datetime(2030, 1, 3, 12, tzinfo=UTC)
    with seeded_session.begin():
        seeded_session.add_all(
            Lot(
                lot_no=lot_no,
                lot_type="production",
                part_no="PN-ABF-1001",
                panel_count=5,
                released_at=arrival - timedelta(days=1),
            )
            for lot_no in (bridge, child)
        )
        seeded_session.flush()
        seeded_session.add_all(
            LotLink(parent_lot_no=source, child_lot_no=target, relation="split")
            for source, target in ((SAMPLE_LOT, child), (other_parent, bridge), (bridge, child))
        )
        sample = seeded_session.scalars(
            select(LotMove).where(LotMove.lot_no == SAMPLE_LOT, LotMove.station_code == "B60")
        ).one()
        seeded_session.add(
            LotMove(
                lot_no=child,
                station_code=sample.station_code,
                machine_code=sample.machine_code,
                start_at=arrival,
                end_at=arrival + timedelta(hours=1),
                operator=sample.operator,
            )
        )

    params = {"from": arrival.isoformat(), "to": (arrival + timedelta(hours=1)).isoformat()}
    nearest = client.get(f"/lots/{child}").json()["qtime"][0]
    assert (nearest["from_lot_no"], nearest["status"]) == (SAMPLE_LOT, "over_limit")
    assert client.get("/qtime", params=params).json()["items"] == [nearest]

    with seeded_session.begin():
        seeded_session.add(
            LotLink(parent_lot_no=other_parent, child_lot_no=child, relation="split")
        )

    ambiguous = client.get(f"/lots/{child}").json()["qtime"][0]
    assert (ambiguous["from_lot_no"], ambiguous["status"], ambiguous["hours"]) == (
        None,
        "incomplete",
        None,
    )
    assert client.get("/qtime", params=params).json()["items"] == [ambiguous]
