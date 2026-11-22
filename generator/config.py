"""Load scenario and dirty-data settings from TOML files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    machine_prefix: str
    machines: int
    queue_hours: tuple[float, float]
    process_hours: tuple[float, float]
    source: str | None = None

    def machine_codes(self) -> list[str]:
        return [f"{self.machine_prefix}-{i:02d}" for i in range(1, self.machines + 1)]


@dataclass(frozen=True)
class Item:
    code: str
    name: str
    station: str
    kind: str  # "continuous" | "count"
    unit: str
    target: float
    usl: float
    physical_min: float
    lsl: float | None = None
    sd: float = 0.0
    mean: float = 0.0
    decimals: int = 0
    simulation_mean: float | None = None
    sample_panels: int | None = None


@dataclass(frozen=True)
class Part:
    part_no: str
    name: str
    line: str


@dataclass
class Config:
    scenario: dict[str, Any]
    scale: dict[str, int]
    materials: dict[str, Any]
    parts: list[Part]
    stations: list[Station]
    items: list[Item]
    events: dict[str, dict[str, Any]]
    dirty: dict[str, Any] = field(default_factory=dict)

    def station(self, code: str) -> Station:
        return next(s for s in self.stations if s.code == code)

    def items_for(self, station_code: str) -> list[Item]:
        return [i for i in self.items if i.station == station_code]

    def item(self, code: str) -> Item:
        return next(i for i in self.items if i.code == code)

    def material_rates(self) -> dict[str, int]:
        if "kinds" in self.materials:
            return {entry["code"]: entry["lots_per_material"] for entry in self.materials["kinds"]}
        return {"ABF": self.materials["abf_lots_per_material"], "CORE": self.materials["core_lots_per_material"]}

    def qtime_route(self) -> tuple[str, str] | None:
        event = self.events["qtime_over"]
        start, end = event.get("from_station", "B20"), event.get("to_station", "B60")
        station_codes = {station.code for station in self.stations}
        if event.get("enabled", True) and {start, end} <= station_codes:
            return start, end
        return None


def load_config(
    scenario_path: Path | None = None, dirty_path: Path | None = None
) -> Config:
    scenario_path = scenario_path or CONFIG_DIR / "scenario.toml"
    dirty_path = dirty_path or CONFIG_DIR / "dirty.toml"
    raw = tomllib.loads(scenario_path.read_text(encoding="utf-8"))
    dirty = tomllib.loads(dirty_path.read_text(encoding="utf-8"))
    return build_config(raw, dirty)


def build_config(raw: dict[str, Any], dirty: dict[str, Any]) -> Config:
    stations = [
        Station(
            code=s["code"],
            name=s["name"],
            machine_prefix=s["machine_prefix"],
            machines=s["machines"],
            queue_hours=tuple(s["queue_hours"]),
            process_hours=tuple(s["process_hours"]),
            source=s.get("source"),
        )
        for s in raw["stations"]
    ]
    items = [
        Item(
            code=i["code"],
            name=i["name"],
            station=i["station"],
            kind=i["kind"],
            unit=i["unit"],
            target=i["target"],
            usl=i["usl"],
            physical_min=i["physical_min"],
            lsl=i.get("lsl"),
            sd=i.get("sd", 0.0),
            mean=i.get("mean", 0.0),
            decimals=i.get("decimals", 0),
            simulation_mean=i.get("simulation_mean"),
            sample_panels=i.get("sample_panels"),
        )
        for i in raw["items"]
    ]
    parts = [Part(**p) for p in raw["parts"]]
    return Config(
        scenario=raw["scenario"],
        scale=raw["scale"],
        materials=raw["materials"],
        parts=parts,
        stations=stations,
        items=items,
        events=raw["events"],
        dirty=dirty,
    )
