"""C40 header mapping follows the project decisions for exact names and collisions."""

from pathlib import Path

import pytest

from inspection_trace.ingestion.errors import E_HEADER_CONFLICT, E_HEADER_UNKNOWN
from inspection_trace.ingestion.mapping import (
    HeaderMappingError,
    UnusedColumn,
    inspect_c40_headers,
    map_c40_headers,
)
from inspection_trace.ingestion.readers import read_csv

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "files" / "MES_OLD_C40_1151103.csv"
HEADERS = ("LOTNO", "PNL", "PT", "EQP", "ITEM", "VAL", "UOM", "DT", "TM", "OP")


def test_c40_sample_maps_each_original_column_to_its_system_field() -> None:
    document = read_csv(SAMPLE.read_bytes(), encoding="cp950")

    assert map_c40_headers(document.header.cells) == {
        "lot_no": 0,
        "panel": 1,
        "point": 2,
        "machine_code": 3,
        "item_code": 4,
        "value": 5,
        "unit": 6,
        "date": 7,
        "time": 8,
        "operator": 9,
    }


@pytest.mark.parametrize("alias", ["VALUE", "DATA"])
def test_accepts_an_explicit_value_alias_in_a_different_position(alias: str) -> None:
    headers = (alias, *HEADERS[:5], *HEADERS[6:])

    assert map_c40_headers(headers)["value"] == 0


@pytest.mark.parametrize("replacement", ["val", " VAL ", "VALL"])
def test_unregistered_value_header_leaves_the_required_field_missing(replacement: str) -> None:
    headers = (*HEADERS[:5], replacement, *HEADERS[6:])

    with pytest.raises(HeaderMappingError) as error:
        map_c40_headers(headers)

    assert error.value.code == E_HEADER_UNKNOWN


@pytest.mark.parametrize("duplicate", ["DATA", "VAL"])
def test_rejects_two_columns_mapped_to_the_same_field(duplicate: str) -> None:
    with pytest.raises(HeaderMappingError) as error:
        map_c40_headers((*HEADERS, duplicate))

    assert error.value.code == E_HEADER_CONFLICT


def test_rejects_c40_header_without_operator() -> None:
    with pytest.raises(HeaderMappingError) as error:
        map_c40_headers(HEADERS[:-1])

    assert error.value.code == E_HEADER_UNKNOWN


def test_reports_extra_columns_without_shifting_mapped_values() -> None:
    report = inspect_c40_headers((*HEADERS[:5], "SHIFT", *HEADERS[5:], "COMMENT"))

    assert report.positions["value"] == 6
    assert report.unused_columns == (UnusedColumn(6, "SHIFT"), UnusedColumn(12, "COMMENT"))


def test_known_headers_need_no_unused_column_warning() -> None:
    assert inspect_c40_headers(HEADERS).unused_columns == ()
