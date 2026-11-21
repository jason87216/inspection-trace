from scripts.evaluate_detection import match_errors


def test_error_scoring_matches_code_and_source_coordinates() -> None:
    expected = [(41, "VAL", "E_REQUIRED_MISSING"), (78, "VAL", "E_NOT_NUMERIC")]
    actual = [(41, "VAL", "E_REQUIRED_MISSING"), (78, "UOM", "E_NOT_NUMERIC")]

    hits, missed, extra = match_errors(expected, actual)

    assert hits == {"E_REQUIRED_MISSING": 1}
    assert missed == {"E_NOT_NUMERIC": 1}
    assert extra == {"E_NOT_NUMERIC": 1}
