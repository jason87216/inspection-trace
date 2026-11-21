"""Master data: stations, machines, items, parts, operators, material lots."""

from __future__ import annotations

import random
from dataclasses import dataclass

from generator.config import Config


@dataclass(frozen=True)
class MaterialLot:
    lot_no: str
    kind: str  # "ABF" | "CORE"
    index: int


@dataclass
class MasterData:
    machines: dict[str, list[str]]  # station code -> machine codes
    operators: list[str]
    abf_lots: list[MaterialLot]
    core_lots: list[MaterialLot]


def build_master(cfg: Config, lot_count: int, rng: random.Random) -> MasterData:
    machines = {s.code: s.machine_codes() for s in cfg.stations}
    operators = [f"OP-{i:04d}" for i in range(1, cfg.scenario["operators"] + 1)]

    abf_needed = lot_count // cfg.materials["abf_lots_per_material"] + 1
    core_needed = lot_count // cfg.materials["core_lots_per_material"] + 1
    abf_start = rng.randint(1000, 4000)
    core_start = rng.randint(5000, 8000)
    abf_lots = [MaterialLot(f"ABF-{abf_start + i}", "ABF", i) for i in range(abf_needed)]
    core_lots = [
        MaterialLot(f"CORE-{core_start + i}", "CORE", i) for i in range(core_needed)
    ]
    return MasterData(machines, operators, abf_lots, core_lots)
