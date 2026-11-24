"""Export scenario-preset previews for a static, backend-free demo page.

Uses the same `preview()` checks as the web UI's 模擬情境 page on the
built-in ABF preset and a few documented edits. No database is needed.

    uv run python -m scripts.export_scenario_demo --out out/inspection-trace-scenarios.json
"""

import argparse
import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspection_trace.scenario_schema import Preset, builtin, preview
from scripts.export_demo import _commit

FORMAT_VERSION = 1
SCALES = ("small", "medium", "large")

type Edit = Callable[[dict[str, Any]], None]


def _set_sample_panels(config: dict[str, Any]) -> None:
    config["scenario"]["sample_panels"] = 25


def _set_target_above_usl(config: dict[str, Any]) -> None:
    item = next(row for row in config["items"] if row["code"] == "SR_THK")
    item["target"] = 30


def _disable_material_shift(config: dict[str, Any]) -> None:
    config["events"]["material_shift"]["enabled"] = False


def _oversize(config: dict[str, Any]) -> None:
    config["scenario"].update(panels_per_lot=200, sample_panels=40, points_per_panel=20)


# (id, title, what changed, scale, edit)
EDITS: tuple[tuple[str, str, str, str, Edit], ...] = (
    (
        "sample_over_lot",
        "抽樣片數超過每批片數",
        "每批 20 片，抽樣片數改成 25 片",
        "medium",
        _set_sample_panels,
    ),
    (
        "target_above_usl",
        "目標值超出規格上限",
        "防焊厚度 SR_THK 規格上限 25 μm，目標值改成 30 μm",
        "medium",
        _set_target_above_usl,
    ),
    (
        "too_many_records",
        "資料量超過上限",
        "大型規模下，每批 200 片、抽樣 40 片、每片 20 點",
        "large",
        _oversize,
    ),
    (
        "material_shift_off",
        "關閉原料突變事件",
        "小型規模只有 8 批，關閉尚未發生的原料突變",
        "small",
        _disable_material_shift,
    ),
)


def _edited(base: Preset, edit: Edit) -> Preset:
    data = copy.deepcopy(base.model_dump())
    edit(data["config"])
    return Preset.model_validate(data)


def _preset_summary(preset: Preset) -> dict[str, Any]:
    config = preset.config
    return {
        "name": preset.name,
        "scenario": config.scenario.model_dump(exclude={"lot_pattern"}),
        "scale": config.scale.model_dump(),
        "materials": [kind.model_dump() for kind in config.materials.kinds],
        "stations": [
            station.model_dump(include={"code", "name", "machines", "source"})
            for station in config.stations
        ],
        "items": [
            item.model_dump(include={"code", "name", "station", "unit", "lsl", "target", "usl"})
            for item in config.items
        ],
        "events": {name: event["enabled"] for name, event in config.events.model_dump().items()},
    }


def export(base: Preset | None = None) -> dict[str, Any]:
    preset = base or builtin()
    return {
        "formatVersion": FORMAT_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "commit": _commit(),
        "preset": _preset_summary(preset),
        "previews": {scale: preview(preset, scale).model_dump() for scale in SCALES},
        "edits": [
            {
                "id": edit_id,
                "title": title,
                "change": change,
                "scale": scale,
                "preview": preview(_edited(preset, edit), scale).model_dump(),
            }
            for edit_id, title, change, scale, edit in EDITS
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out/inspection-trace-scenarios.json"))
    args = parser.parse_args()
    data = export()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    edits = ", ".join(
        f"{row['id']}: {len(row['preview']['errors'])} errors" for row in data["edits"]
    )
    print(f"wrote {args.out} ({edits})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
