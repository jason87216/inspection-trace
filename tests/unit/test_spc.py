import pytest

from inspection_trace.spc.chart import build_chart


def test_moving_ranges_and_limits_use_only_the_baseline() -> None:
    result = build_chart([9, 11, 10, 14, 10], baseline_points=3)

    assert result.individual_limits.center == 10
    assert result.individual_limits.upper == pytest.approx(10 + 3 * 1.5 / 1.128)
    assert result.individual_limits.lower == pytest.approx(10 - 3 * 1.5 / 1.128)
    assert result.range_limits.center == 1.5
    assert result.range_limits.upper == pytest.approx(3.267 * 1.5)
    assert result.range_limits.lower == 0
    assert [point.moving_range for point in result.points] == [None, 2, 1, 4, 4]
    assert result.points[3].signals == ("I_OUTSIDE_3SIGMA",)


@pytest.mark.parametrize("side", [-1, 1])
def test_eight_same_side_fires_once_and_a_center_point_resets_the_run(side: int) -> None:
    baseline = [9, 11]
    uninterrupted = [10 + side] * 9
    result = build_chart(baseline + uninterrupted + [10] + uninterrupted, baseline_points=2)

    assert [i for i, point in enumerate(result.points) if point.signals] == [9, 19]
    assert result.points[9].signals == ("I_EIGHT_SAME_SIDE",)


def test_missing_baseline_and_zero_variation_have_defined_results() -> None:
    short = build_chart([10], baseline_points=2)
    assert short.individual_limits is None
    assert short.range_limits is None
    assert short.points[0].signals == ()

    flat = build_chart([10, 10, 10, 11, 11], baseline_points=3)
    assert flat.points[2].signals == ()
    assert flat.points[3].signals == ("I_OUTSIDE_3SIGMA", "MR_OUTSIDE_LIMIT")
    assert flat.points[4].signals == ("I_OUTSIDE_3SIGMA",)
