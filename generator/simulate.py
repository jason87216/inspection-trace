"""Simulate lots flowing through the process and produce ground-truth records.

Everything here is the "truth": clean values plus the process events that
explain them. Rendering and dirtying happen later and never change the truth.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from generator.config import Config, Item
from generator.master import MasterData, MaterialLot


@dataclass
class Lot:
    lot_no: str
    part_no: str
    panels: list[int]
    abf_lot: str
    core_lot: str
    release: datetime
    abf_index: int
    parent: str | None = None
    qtime_hours: float | None = None


@dataclass
class Move:
    lot_no: str
    station: str
    machine: str
    start: datetime
    end: datetime
    operator: str


@dataclass
class Record:
    record_id: int
    lot_no: str
    part_no: str
    station: str
    machine: str
    item: str
    panel: int
    point: int | None
    value: float | int
    unit: str
    measured_at: datetime
    operator: str
    shift: str


@dataclass
class Link:
    parent: str
    child: str
    relation: str  # "consume" | "split"


@dataclass
class SimResult:
    lots: list[Lot] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm; fine for the small means used here."""
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def shift_of(ts: datetime) -> str:
    return "日班" if 8 <= ts.hour < 20 else "夜班"


class Simulator:
    def __init__(self, cfg: Config, master: MasterData, rng: random.Random) -> None:
        self.cfg = cfg
        self.master = master
        self.rng = rng
        self.result = SimResult()
        self.next_record_id = 1
        self.routing = [s.code for s in cfg.stations]
        ev = cfg.events
        self.drift = ev["drift"]
        self.mat = ev["material_shift"]
        self.bath = ev["bath_aging"]
        self.qtime = ev["qtime_over"]
        self.spike = ev["oos_spike"]
        self.split = ev["split"]
        self.qtime_limit = cfg.scenario["qtime_limit_hours"]
        # fixed per-machine bias for every measured item
        self.bias: dict[tuple[str, str], float] = {}
        for item in cfg.items:
            if item.kind != "continuous":
                continue
            for m in master.machines[item.station]:
                self.bias[(m, item.code)] = rng.gauss(0, 0.25 * item.sd)
        # event state
        self.drift_count = {m: 0 for m in self.drift["machines"]}
        self.drift_segment: dict[str, dict[str, Any]] = {}
        self.bath_count: dict[str, int] = {}
        self.bath_segment: dict[str, dict[str, Any]] = {}
        self.bath_no: dict[str, int] = {}
        self.mat_event: dict[str, Any] | None = None

    # ------------------------------------------------------------------ lots
    def make_lots(self, count: int) -> list[Lot]:
        sc = self.cfg.scenario
        start = datetime.fromisoformat(sc["start"])
        interval = timedelta(hours=sc["release_interval_hours"])
        seq_per_day: dict[str, int] = {}
        lots = []
        abf_per = self.cfg.materials["abf_lots_per_material"]
        core_per = self.cfg.materials["core_lots_per_material"]
        for i in range(count):
            release = start + i * interval
            part = self.rng.choice(self.cfg.parts)
            day = release.strftime("%y%m%d")
            seq_per_day[day] = seq_per_day.get(day, 0) + 1
            lot_no = f"L{day}{part.line}{seq_per_day[day]:03d}"
            abf: MaterialLot = self.master.abf_lots[i // abf_per]
            core: MaterialLot = self.master.core_lots[i // core_per]
            lots.append(
                Lot(
                    lot_no=lot_no,
                    part_no=part.part_no,
                    panels=list(range(1, sc["panels_per_lot"] + 1)),
                    abf_lot=abf.lot_no,
                    core_lot=core.lot_no,
                    release=release,
                    abf_index=abf.index,
                )
            )
        return lots

    # ------------------------------------------------------------------ run
    def run(self, lot_count: int) -> SimResult:
        res = self.result
        for lot in self.make_lots(lot_count):
            res.lots.append(lot)
            res.links.append(Link(lot.abf_lot, lot.lot_no, "consume"))
            res.links.append(Link(lot.core_lot, lot.lot_no, "consume"))
            force_qtime = self.rng.random() < self.qtime["probability"]
            self._walk(lot, 0, lot.release, force_qtime, b20_end=None)
        self._close_segments()
        return res

    def _walk(
        self,
        lot: Lot,
        start_index: int,
        t: datetime,
        force_qtime: bool,
        b20_end: datetime | None,
    ) -> None:
        for idx in range(start_index, len(self.routing)):
            code = self.routing[idx]
            if (
                code == self.split["at_station"]
                and lot.parent is None
                and self.rng.random() < self.split["probability"]
            ):
                child = self._split(lot)
                self._walk(child, idx, t, False, b20_end)
            st = self.cfg.station(code)
            queue = max(0.05, self.rng.gauss(*st.queue_hours))
            if code == "B60" and force_qtime:
                queue += self.rng.uniform(*self.qtime["extra_wait_hours"])
            start = t + timedelta(hours=queue)
            process = max(0.1, self.rng.gauss(*st.process_hours))
            end = start + timedelta(hours=process)
            machine = self.rng.choice(self.master.machines[code])
            operator = self.rng.choice(self.master.operators)
            self.result.moves.append(Move(lot.lot_no, code, machine, start, end, operator))

            if code == "B20":
                b20_end = end
            if code == "B60" and b20_end is not None:
                lot.qtime_hours = round((start - b20_end).total_seconds() / 3600, 2)
                if lot.qtime_hours > self.qtime_limit:
                    self.result.events.append(
                        {
                            "event": "EV_QTIME_OVER",
                            "lot_no": lot.lot_no,
                            "from_station": "B20",
                            "to_station": "B60",
                            "qtime_hours": lot.qtime_hours,
                            "limit_hours": self.qtime_limit,
                            "affected_items": self.qtime["affected_items"],
                            "mean_multiplier": self.qtime["mean_multiplier"],
                        }
                    )
            self._measure(lot, code, machine, operator, start, end)
            self._after_station(lot, code, machine, end)
            t = end

    def _split(self, lot: Lot) -> Lot:
        k = self.split["child_panels"]
        child_panels = lot.panels[-k:]
        lot.panels = lot.panels[:-k]
        child = Lot(
            lot_no=f"{lot.lot_no}-S1",
            part_no=lot.part_no,
            panels=child_panels,
            abf_lot=lot.abf_lot,
            core_lot=lot.core_lot,
            release=lot.release,
            abf_index=lot.abf_index,
            parent=lot.lot_no,
            qtime_hours=lot.qtime_hours,
        )
        self.result.lots.append(child)
        self.result.links.append(Link(lot.lot_no, child.lot_no, "split"))
        return child

    # ------------------------------------------------------------ measuring
    def _measure(
        self,
        lot: Lot,
        station: str,
        machine: str,
        operator: str,
        start: datetime,
        end: datetime,
    ) -> None:
        sc = self.cfg.scenario
        for item in self.cfg.items_for(station):
            if item.kind == "count":
                mult = 1.0
                if (
                    lot.qtime_hours is not None
                    and lot.qtime_hours > self.qtime_limit
                    and item.code in self.qtime["affected_items"]
                ):
                    mult = self.qtime["mean_multiplier"]
                for panel in lot.panels:
                    value = poisson(self.rng, item.mean * mult)
                    self._add(lot, station, machine, item, panel, None, value, operator, start, end)
            else:
                n = min(sc["sample_panels"], len(lot.panels))
                for panel in sorted(self.rng.sample(lot.panels, n)):
                    for point in range(1, sc["points_per_panel"] + 1):
                        value = self._continuous_value(lot, item, machine)
                        rid = self._add(
                            lot, station, machine, item, panel, point, value, operator, start, end
                        )
                        self._maybe_spike(rid, item)

    def _continuous_value(self, lot: Lot, item: Item, machine: str) -> float:
        mean = item.target + self.bias[(machine, item.code)]
        sd = item.sd
        if machine in self.drift_count and item.code in self.drift["items"]:
            mean += self.drift["rate_per_lot"] * self.drift_count[machine]
        if item.code == self.mat["item"] and lot.abf_index >= self.mat["from_material_index"]:
            mean += self.mat["delta"]
        if item.code in self.bath["items"]:
            sd *= 1 + self.bath["aging_per_lot"] * self.bath_count.get(machine, 0)
        return round(self.rng.gauss(mean, sd), item.decimals)

    def _add(
        self,
        lot: Lot,
        station: str,
        machine: str,
        item: Item,
        panel: int,
        point: int | None,
        value: float | int,
        operator: str,
        start: datetime,
        end: datetime,
    ) -> int:
        span = max(1, int((end - start).total_seconds()))
        measured_at = start + timedelta(seconds=self.rng.randint(0, span))
        measured_at = measured_at.replace(microsecond=0)
        rec = Record(
            record_id=self.next_record_id,
            lot_no=lot.lot_no,
            part_no=lot.part_no,
            station=station,
            machine=machine,
            item=item.code,
            panel=panel,
            point=point,
            value=value,
            unit=item.unit,
            measured_at=measured_at,
            operator=operator,
            shift=shift_of(measured_at),
        )
        self.result.records.append(rec)
        self.next_record_id += 1
        return rec.record_id

    def _maybe_spike(self, record_id: int, item: Item) -> None:
        if self.rng.random() >= self.spike["probability_per_value"]:
            return
        rec = self.result.records[-1]
        direction = self.rng.choice([-1, 1])
        rec.value = round(rec.value + direction * self.spike["sigma"] * item.sd, item.decimals)
        if rec.value < item.physical_min:  # keep it physically possible
            rec.value = round(item.physical_min + item.sd, item.decimals)
        self.result.events.append(
            {
                "event": "EV_OOS_SPIKE",
                "record_id": record_id,
                "lot_no": rec.lot_no,
                "item": item.code,
                "value": rec.value,
                "direction": "high" if direction > 0 else "low",
            }
        )

    # ------------------------------------------------------- event bookkeeping
    def _after_station(self, lot: Lot, station: str, machine: str, end: datetime) -> None:
        if machine in self.drift_count:
            seg = self.drift_segment.setdefault(
                machine, {"start": end, "lots": []}
            )
            seg["lots"].append(lot.lot_no)
            seg["end"] = end
            self.drift_count[machine] += 1
            if self.drift_count[machine] >= self.drift["pm_interval_lots"]:
                self._close_drift(machine)
                self.result.events.append(
                    {"event": "EV_PM_RESET", "machine": machine, "at": end.isoformat()}
                )
                self.drift_count[machine] = 0

        if station == self.cfg.item(self.bath["items"][0]).station:
            seg = self.bath_segment.setdefault(
                machine,
                {"start": end, "lots": [], "bath_no": self.bath_no.get(machine, 1)},
            )
            seg["lots"].append(lot.lot_no)
            seg["end"] = end
            self.bath_count[machine] = self.bath_count.get(machine, 0) + 1
            if self.bath_count[machine] >= self.bath["bath_interval_lots"]:
                self._close_bath(machine)
                self.bath_count[machine] = 0
                self.bath_no[machine] = self.bath_no.get(machine, 1) + 1

        mat_station = self.cfg.item(self.mat["item"]).station
        if station == mat_station and lot.abf_index >= self.mat["from_material_index"]:
            if self.mat_event is None:
                self.mat_event = {
                    "event": "EV_MAT_SHIFT",
                    "material_lot": lot.abf_lot,
                    "item": self.mat["item"],
                    "delta": self.mat["delta"],
                    "start": end.isoformat(),
                    "lots": [],
                }
                self.result.events.append(self.mat_event)
            self.mat_event["lots"].append(lot.lot_no)

    def _close_drift(self, machine: str) -> None:
        seg = self.drift_segment.pop(machine, None)
        if not seg:
            return
        self.result.events.append(
            {
                "event": "EV_DRIFT",
                "machine": machine,
                "items": self.drift["items"],
                "rate_per_lot": self.drift["rate_per_lot"],
                "start": seg["start"].isoformat(),
                "end": seg["end"].isoformat(),
                "lots": seg["lots"],
            }
        )

    def _close_bath(self, machine: str) -> None:
        seg = self.bath_segment.pop(machine, None)
        if not seg:
            return
        self.result.events.append(
            {
                "event": "EV_BATH_AGING",
                "machine": machine,
                "bath_no": seg["bath_no"],
                "items": self.bath["items"],
                "start": seg["start"].isoformat(),
                "end": seg["end"].isoformat(),
                "lots": seg["lots"],
                "max_sd_multiplier": round(
                    1 + self.bath["aging_per_lot"] * (len(seg["lots"]) - 1), 2
                ),
            }
        )

    def _close_segments(self) -> None:
        for m in list(self.drift_segment):
            self._close_drift(m)
        for m in list(self.bath_segment):
            self._close_bath(m)


def simulate(cfg: Config, master: MasterData, lot_count: int, rng: random.Random) -> SimResult:
    return Simulator(cfg, master, rng).run(lot_count)


# ---------------------------------------------------------------- truth output
def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def write_truth(cfg: Config, master: MasterData, res: SimResult, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    def dump(name: str, rows: list[dict[str, Any]]) -> None:
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            if not rows:
                return
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: _iso(v) for k, v in r.items()})

    dump(
        "stations.csv",
        [
            {"code": s.code, "name": s.name, "seq": i + 1, "source": s.source or ""}
            for i, s in enumerate(cfg.stations)
        ],
    )
    dump(
        "machines.csv",
        [{"code": m, "station": s} for s, ms in master.machines.items() for m in ms],
    )
    dump(
        "items.csv",
        [
            {
                "code": i.code,
                "name": i.name,
                "station": i.station,
                "kind": i.kind,
                "unit": i.unit,
                "lsl": "" if i.lsl is None else i.lsl,
                "target": i.target,
                "usl": i.usl,
                "physical_min": i.physical_min,
            }
            for i in cfg.items
        ],
    )
    dump("parts.csv", [asdict(p) for p in cfg.parts])
    dump(
        "materials.csv",
        [{"lot_no": m.lot_no, "kind": m.kind} for m in master.abf_lots + master.core_lots],
    )
    dump("operators.csv", [{"code": o} for o in master.operators])
    dump(
        "lots.csv",
        [
            {
                "lot_no": lot.lot_no,
                "part_no": lot.part_no,
                "panel_count": len(lot.panels),
                "abf_lot": lot.abf_lot,
                "core_lot": lot.core_lot,
                "release": lot.release,
                "parent": lot.parent or "",
                "qtime_hours": "" if lot.qtime_hours is None else lot.qtime_hours,
            }
            for lot in res.lots
        ],
    )
    dump("lot_moves.csv", [asdict(m) for m in res.moves])
    dump("lot_links.csv", [asdict(link) for link in res.links])
    dump("records.csv", [asdict(r) for r in res.records])
    (out / "events.answers.json").write_text(
        json.dumps(res.events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
