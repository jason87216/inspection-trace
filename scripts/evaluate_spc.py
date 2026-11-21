"""Check I-chart signals against the generator's drift and material-shift events."""

from random import Random

from generator.config import load_config
from generator.master import build_master
from generator.simulate import simulate
from inspection_trace.spc.chart import build_chart


def main() -> int:
    config = load_config()
    rng = Random(42)
    lot_count = config.scale["medium"]
    simulated = simulate(config, build_master(config, lot_count, rng), lot_count, rng)
    cases = (
        ("EV_DRIFT", "LDR-01", "VIA_TOP_D", -1),
        ("EV_MAT_SHIFT", "LAM-01", "ABF_THK", 1),
    )
    print("Simulation: medium / seed 42 / first 20 observations as baseline")
    print("| Event | Machine | Item | Windows detected |")
    print("| --- | --- | --- | ---: |")
    missed = False
    for event_type, machine, item, direction in cases:
        records = sorted(
            (row for row in simulated.records if row.machine == machine and row.item == item),
            key=lambda row: (row.measured_at, row.lot_no, row.panel, row.point or 0),
        )
        chart = build_chart([row.value for row in records], baseline_points=20)
        limits = chart.individual_limits
        if limits is None:
            raise ValueError(f"Not enough baseline observations for {machine} / {item}.")
        alerted_lots = {
            row.lot_no
            for row, point in zip(records, chart.points, strict=True)
            if direction * (row.value - limits.center) > 0
            and ("I_OUTSIDE_3SIGMA" in point.signals or "I_EIGHT_SAME_SIDE" in point.signals)
        }
        observed_lots = {row.lot_no for row in records}
        windows = [
            window
            for event in simulated.events
            if event["event"] == event_type and event.get("machine", machine) == machine
            if (window := observed_lots.intersection(event["lots"]))
        ]
        hits = sum(bool(window & alerted_lots) for window in windows)
        print(f"| {event_type} | {machine} | {item} | {hits}/{len(windows)} |")
        missed = missed or not windows or hits != len(windows)
    return int(missed)


if __name__ == "__main__":
    raise SystemExit(main())
