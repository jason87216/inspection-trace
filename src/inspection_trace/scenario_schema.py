"""Editable scenario format and checks shared by preview, save, and generation."""

import re
import tomllib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from generator.config import CONFIG_DIR

type Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]{0,19}$")]
type Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
type Probability = Annotated[float, Field(ge=0, le=1)]
type Hours = Annotated[float, Field(ge=0, le=240)]
type Measurement = Annotated[float, Field(ge=0, le=1_000_000)]
type Delta = Annotated[float, Field(ge=-1_000_000, le=1_000_000)]
type PositiveCount = Annotated[int, Field(ge=1, le=600)]
type Source = Literal["SRC_MACHINE", "SRC_STATION", "SRC_MANUAL", "SRC_LEGACY"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Production(Settings):
    company: Label
    start: Annotated[str, Field(max_length=32)]
    release_interval_hours: Annotated[float, Field(ge=0.025, le=240)]
    panels_per_lot: Annotated[int, Field(ge=1, le=200)]
    sample_panels: Annotated[int, Field(ge=1, le=200)]
    points_per_panel: Annotated[int, Field(ge=1, le=20)]
    qtime_limit_hours: Annotated[float, Field(gt=0, le=240)]
    operators: Annotated[int, Field(ge=1, le=9999)]
    lot_pattern: Literal[r"^L\d{6}[A-Z]\d{3}(-S\d)?$"] = r"^L\d{6}[A-Z]\d{3}(-S\d)?$"


class Scale(Settings):
    small: PositiveCount
    medium: PositiveCount
    large: PositiveCount


class Material(Settings):
    code: Annotated[Code, Field(max_length=10)]
    lots_per_material: PositiveCount


class Materials(Settings):
    kinds: Annotated[list[Material], Field(max_length=8)]


class Part(Settings):
    part_no: Code
    name: Label
    line: Annotated[str, Field(pattern=r"^[A-Z]$")]


class Station(Settings):
    code: Code
    name: Label
    machine_prefix: Annotated[Code, Field(max_length=17)]
    machines: Annotated[int, Field(ge=1, le=30)]
    queue_hours: tuple[Hours, Hours]
    process_hours: tuple[Hours, Hours]
    source: Source | None = None


class Item(Settings):
    code: Code
    name: Label
    station: Code
    kind: Literal["continuous", "count"]
    unit: Annotated[str, StringConstraints(min_length=1, max_length=10, strip_whitespace=True)]
    lsl: Measurement | None = None
    target: Measurement
    usl: Measurement
    physical_min: Measurement
    sd: Measurement = 0
    mean: Annotated[float, Field(ge=0, le=100)] = 0
    decimals: Annotated[int, Field(ge=0, le=3)] = 0
    simulation_mean: Measurement | None = None
    sample_panels: Annotated[int, Field(ge=1, le=200)] | None = None


class Drift(Settings):
    enabled: bool = True
    machines: Annotated[list[Code], Field(max_length=30)]
    items: Annotated[list[Code], Field(max_length=40)]
    rate_per_lot: Delta
    pm_interval_lots: PositiveCount


class MaterialShift(Settings):
    enabled: bool = True
    material_kind: Code = "ABF"
    item: Code
    from_material_index: Annotated[int, Field(ge=0, le=599)]
    delta: Delta


class Bath(Settings):
    enabled: bool = True
    items: Annotated[list[Code], Field(max_length=40)]
    aging_per_lot: Annotated[float, Field(ge=0, le=5)]
    bath_interval_lots: PositiveCount


class Wait(Settings):
    enabled: bool = True
    from_station: Code = "B20"
    to_station: Code = "B60"
    probability: Probability
    extra_wait_hours: tuple[Hours, Hours]
    affected_items: Annotated[list[Code], Field(max_length=40)]
    mean_multiplier: Annotated[float, Field(ge=1, le=10)]


class Spike(Settings):
    enabled: bool = True
    probability_per_value: Probability
    sigma: Annotated[float, Field(gt=0, le=100)]
    items: Annotated[list[Code], Field(max_length=80)] = Field(default_factory=list)


class Split(Settings):
    enabled: bool = True
    probability: Probability
    at_station: Code
    child_panels: Annotated[int, Field(ge=1, le=199)]


class Events(Settings):
    drift: Drift
    material_shift: MaterialShift
    bath_aging: Bath
    qtime_over: Wait
    oos_spike: Spike
    split: Split


class ConfigDocument(Settings):
    scenario: Production
    scale: Scale
    materials: Materials
    parts: Annotated[list[Part], Field(min_length=1, max_length=40)]
    stations: Annotated[list[Station], Field(min_length=1, max_length=40)]
    items: Annotated[list[Item], Field(min_length=1, max_length=80)]
    events: Events


class DirtyFiles(Settings):
    clean_ratio: Probability
    duplicate_ratio: Probability


class DirtyDocument(Settings):
    files: DirtyFiles
    row_rates: dict[Source, Probability]
    ops: dict[Source, dict[str, Annotated[float, Field(ge=0, le=100)]]]
    file_ops: dict[str, Probability]


class Preset(Settings):
    name: Label
    config: ConfigDocument
    dirty: DirtyDocument


def builtin() -> Preset:
    raw = tomllib.loads((CONFIG_DIR / "scenario.toml").read_text(encoding="utf-8"))
    dirty = tomllib.loads((CONFIG_DIR / "dirty.toml").read_text(encoding="utf-8"))
    return parse_configuration("ABF 載板製程", raw, dirty)


def parse_configuration(name: str, config: dict, dirty: dict) -> Preset:
    materials = config.get("materials")
    if isinstance(materials, dict) and set(materials) == {
        "abf_lots_per_material",
        "core_lots_per_material",
    }:
        config = {
            **config,
            "materials": {
                "kinds": [
                    {"code": code, "lots_per_material": materials[key]}
                    for code, key in (
                        ("ABF", "abf_lots_per_material"),
                        ("CORE", "core_lots_per_material"),
                    )
                ]
            },
        }
    return Preset.model_validate({"name": name, "config": config, "dirty": dirty})


class Preview(BaseModel):
    errors: list[str]
    warnings: list[str]
    lots: int
    estimated_records: int
    maximum_records: int
    formats: list[str]


def preview(preset: Preset, scale: str) -> Preview:
    config = preset.config
    sc, ev = config.scenario, config.events
    errors: list[str] = []
    warnings: list[str] = []
    stations = {station.code: station for station in config.stations}
    sequence = list(stations)
    items = {item.code: item for item in config.items}
    materials = {kind.code: kind.lots_per_material for kind in config.materials.kinds}
    machines = {
        f"{station.machine_prefix}-{index:02d}": station.code
        for station in config.stations
        for index in range(1, station.machines + 1)
    }
    try:
        start = datetime.fromisoformat(sc.start)
        if start.tzinfo or not 1970 <= start.year <= 2098 or "T" not in sc.start:
            raise ValueError()
    except ValueError:
        errors.append("起始時間須為 1970–2098 的台北本地時間，含日期及時間。")
    if sc.sample_panels > sc.panels_per_lot:
        errors.append("抽樣片數不可超過每批總片數。")

    uniqueness = (
        ("站點代碼", [row.code for row in config.stations]),
        ("站點名稱", [row.name.casefold() for row in config.stations]),
        ("機台前綴", [row.machine_prefix for row in config.stations]),
        ("檢驗項目代碼", [row.code for row in config.items]),
        ("同站檢驗項目名稱", [(row.station, row.name.casefold()) for row in config.items]),
        ("料號", [row.part_no for row in config.parts]),
        ("材料分類", [row.code for row in config.materials.kinds]),
    )
    for label, values in uniqueness:
        if len(set(values)) != len(values):
            errors.append(f"{label}不可重複。")

    labels = [sc.company, *(row.name for row in config.parts), *(row.name for row in config.items)]
    labels += [row.name for row in config.stations] + [row.unit for row in config.items]
    if any(re.search(r"^[=+@-]|[\x00-\x1f]", label) for label in labels):
        errors.append("名稱與單位不可包含控制字元或以試算表公式符號開頭。")
    if any(re.search(r'[<>:"/\\|?*]', row.name) for row in config.stations):
        errors.append("站點名稱含檔名不允許的符號。")
    formats: set[str] = set()
    for item in config.items:
        station = stations.get(item.station)
        if station is None or station.source is None:
            errors.append(f"{item.code} 所屬站點不存在或未設定報表來源。")
        else:
            formats.add(station.source)
            required = "count" if station.source == "SRC_MACHINE" else "continuous"
            if item.kind != required:
                errors.append(f"{item.code} 的來源 {station.source} 只支援 {required} 項目。")
            if station.source == "SRC_LEGACY" and item.unit != "μm":
                try:
                    item.unit.encode("cp950")
                except UnicodeEncodeError:
                    errors.append(f"{item.code} 的單位無法寫入舊系統 Big5 報表。")
        minimum = item.physical_min
        lower = minimum if item.lsl is None else item.lsl
        if not minimum <= lower <= item.target <= item.usl:
            errors.append(f"{item.code} 須符合物理下限 ≤ 規格下限 ≤ 目標 ≤ 規格上限。")
        if item.kind == "continuous" and item.simulation_mean is not None:
            if item.simulation_mean < item.physical_min:
                errors.append(f"{item.code} 的模擬平均不可低於物理下限。")
        if item.kind == "count" and item.physical_min != 0:
            errors.append(f"{item.code} 的計數分布從 0 起，物理下限須為 0。")
        if item.kind == "continuous" and minimum != round(minimum, item.decimals):
            errors.append(f"{item.code} 的物理下限須能以設定的量測小數位表示。")
        if item.sample_panels is not None and item.sample_panels > sc.panels_per_lot:
            errors.append(f"{item.code} 的抽樣片數不可超過每批總片數。")

    _check_events(ev, sc, sequence, items, machines, materials, errors)
    _check_dirty(preset.dirty, errors)
    if not materials:
        warnings.append("沒有投入材料，批號追溯仍可顯示分批與站點紀錄。")
    if any(not any(item.station == station for item in items.values()) for station in stations):
        warnings.append("沒有檢驗項目的站點只產生進出站紀錄。")
    lots = getattr(config.scale, scale)
    if ev.material_shift.enabled and ev.material_shift.material_kind in materials:
        first = ev.material_shift.from_material_index * materials[ev.material_shift.material_kind]
        if first >= lots:
            warnings.append("所選批數尚未到達原料突變的開始批次。")
    counts: list[tuple[int, int]] = []
    split_order = sequence.index(ev.split.at_station) if ev.split.at_station in stations else -1
    for item in items.values():
        panels = item.sample_panels or sc.sample_panels
        regular = sc.panels_per_lot if item.kind == "count" else panels * sc.points_per_panel
        max_split = regular
        if (
            ev.split.enabled
            and split_order >= 0
            and item.station in stations
            and sequence.index(item.station) >= split_order
            and item.kind == "continuous"
        ):
            take = min(panels, ev.split.child_panels)
            remain = min(panels, max(0, sc.panels_per_lot - ev.split.child_panels))
            max_split = max(regular, (take + remain) * sc.points_per_panel)
        counts.append((regular, max_split))
    base = lots * sum(row[0] for row in counts)
    upper = lots * sum(row[1] for row in counts)
    estimated = round(base + (upper - base) * ev.split.probability) if ev.split.enabled else base
    if upper > 200_000:
        errors.append("所選規模的量測上限超過 200,000 筆，請減少批數、抽樣或量測點。")
    max_day_lots = min(lots, int(24 / sc.release_interval_hours) + 1)
    for station in stations.values():
        station_count = sum(
            sc.panels_per_lot
            if item.kind == "count"
            else (item.sample_panels or sc.sample_panels) * sc.points_per_panel
            for item in items.values()
            if item.station == station.code
        )
        if station_count * max_day_lots > 8000:
            warnings.append(f"{station.code} 的單份報表可能接近現有匯入大小限制。")
    return Preview(
        errors=errors,
        warnings=warnings,
        lots=lots,
        estimated_records=estimated,
        maximum_records=upper,
        formats=sorted(formats),
    )


def _check_events(
    ev: Events,
    sc: Production,
    sequence: list[str],
    items: dict[str, Item],
    machines: dict[str, str],
    materials: dict[str, int],
    errors: list[str],
) -> None:
    continuous = {code for code, item in items.items() if item.kind == "continuous"}
    if ev.drift.enabled:
        if not ev.drift.items or not ev.drift.machines or not set(ev.drift.items) <= continuous:
            errors.append("量測漂移須指定存在的連續量測項目及機台。")
        elif not set(ev.drift.machines) <= machines.keys() or (
            {items[code].station for code in ev.drift.items}
            != {machines[code] for code in ev.drift.machines}
        ):
            errors.append("量測漂移的機台與檢驗項目須介於相同的站點集合。")
    if ev.material_shift.enabled and (
        ev.material_shift.item not in continuous or ev.material_shift.material_kind not in materials
    ):
        errors.append("原料突變須指定存在的材料分類與連續量測項目。")
    if ev.bath_aging.enabled and (
        not ev.bath_aging.items or not set(ev.bath_aging.items) <= continuous
    ):
        errors.append("槽液老化須指定存在的連續量測項目。")
    q = ev.qtime_over
    if q.enabled:
        if (
            q.from_station not in sequence
            or q.to_station not in sequence
            or sequence.index(q.from_station) >= sequence.index(q.to_station)
        ):
            errors.append("Q-time 的起訖站點必須存在，且起站排在迄站前。")
        elif any(
            code not in items
            or items[code].kind != "count"
            or items[code].station not in sequence[sequence.index(q.to_station) :]
            for code in q.affected_items
        ):
            errors.append("Q-time 影響項目須為迄站或之後的計數項目。")
        if q.extra_wait_hours[0] > q.extra_wait_hours[1]:
            errors.append("Q-time 的額外等待最短時間不可大於最長時間。")
        if any(
            code in items and items[code].mean * q.mean_multiplier > 500
            for code in q.affected_items
        ):
            errors.append("Q-time 超時後的計數平均不可超過 500。")
    if ev.oos_spike.enabled and (not continuous or not set(ev.oos_spike.items) <= continuous):
        errors.append("偶發超規只支援存在的連續量測項目。")
    if ev.split.enabled and (
        ev.split.at_station not in sequence or ev.split.child_panels >= sc.panels_per_lot
    ):
        errors.append("分批站點必須存在，子批片數須小於每批總片數。")


def _check_dirty(dirty: DirtyDocument, errors: list[str]) -> None:
    allowed = tomllib.loads((CONFIG_DIR / "dirty.toml").read_text(encoding="utf-8"))
    if set(dirty.row_rates) != set(allowed["row_rates"]):
        errors.append("髒資料設定必須有四種報表來源的列機率。")
    if set(dirty.file_ops) != set(allowed["file_ops"]):
        errors.append("檔案層級設定必須包含表頭別名與人工版面雜訊。")
    if set(dirty.ops) != set(allowed["ops"]):
        errors.append("髒資料設定必須有四種報表來源的錯誤權重。")
    for source, operations in dirty.ops.items():
        if not set(operations) <= allowed["ops"][source].keys():
            errors.append(f"{source} 含不支援的髒資料操作。")
