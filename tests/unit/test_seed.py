"""First seed exercise: compare station rows without a database."""

from inspection_trace.models import Station
from inspection_trace.seed import compare_stations


def test_compare_stations_reports_all_four_outcomes() -> None:
    source = [
        Station(code="C10", name="裁板", seq=1),
        Station(code="C30", name="鍍通孔", seq=3),
        Station(code="C40", name="內層線路", seq=4),
    ]
    existing = [
        Station(code="C10", name="裁板", seq=1),
        Station(code="C20", name="機械鑽孔", seq=2),
        Station(code="C40", name="內層線路站", seq=4),
    ]

    assert compare_stations(source, existing) == {
        "C10": "unchanged",
        "C20": "database_only",
        "C30": "new",
        "C40": "changed",
    }


def test_compare_stations_reports_sequence_change_without_mutating_rows() -> None:
    source = [Station(code="C40", name="內層線路", seq=4)]
    existing = [Station(code="C40", name="內層線路", seq=5)]

    assert compare_stations(source, existing) == {"C40": "changed"}
    assert source[0].seq == 4
    assert existing[0].seq == 5


def test_compare_stations_ignores_station_active_flag() -> None:
    source = [Station(code="C40", name="內層線路", seq=4)]
    existing = [Station(code="C40", name="內層線路", seq=4, active=False)]

    assert compare_stations(source, existing) == {"C40": "unchanged"}
    assert existing[0].active is False
