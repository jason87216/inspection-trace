"""Master data: stations, machines, items, parts, operators, material lots."""

from __future__ import annotations

import random
from dataclasses import dataclass

from generator.config import Config


@dataclass(frozen=True)
class MaterialLot:
    lot_no: str
    kind: str
    index: int


@dataclass
class MasterData:
    machines: dict[str, list[str]]  # station code -> machine codes
    operators: list[str]
    materials: dict[str, list[MaterialLot]]


def build_master(cfg: Config, lot_count: int, rng: random.Random) -> MasterData:
    machines = {s.code: s.machine_codes() for s in cfg.stations}
    operators = [f"OP-{i:04d}" for i in range(1, cfg.scenario["operators"] + 1)]

    materials = {}
    for kind, per_material in cfg.material_rates().items():
        start = rng.randint(5000, 8000) if kind == "CORE" else rng.randint(1000, 4000)
        materials[kind] = [
            MaterialLot(f"{kind}-{start + i}", kind, i)
            for i in range(lot_count // per_material + 1)
        ]
    return MasterData(machines, operators, materials)
