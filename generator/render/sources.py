"""Render ground-truth records into the four on-site source formats.

SRC_MACHINE  machine export CSV, English headers, UTF-8, epoch timestamps
SRC_STATION  station daily report xlsx, wide rows with P1..P5 points
SRC_MANUAL   hand-copied xlsx with title rows, merged title and a total row
SRC_LEGACY   old-system CSV in Big5 (cp950), abbreviated headers, ROC dates
"""

from __future__ import annotations

from generator.config import Config
from generator.render.common import epoch, fmt_number, group_by, roc_date, shift_day
from generator.simulate import Record, SimResult
from generator.table import DocFile, Roles, Row, Sheet

MACHINE_PREFIX = {"C50": "AOI", "B80": "AOI", "F40": "ET"}


def render_all(cfg: Config, res: SimResult) -> list[DocFile]:
    docs: list[DocFile] = []
    by_station = group_by(res.records, lambda r: r.station)
    for st in cfg.stations:
        recs = by_station.get(st.code, [])
        if not recs or st.source is None:
            continue
        render = {
            "SRC_MACHINE": render_machine,
            "SRC_STATION": render_station,
            "SRC_MANUAL": render_manual,
            "SRC_LEGACY": render_legacy,
        }[st.source]
        docs.extend(render(cfg, st.code, recs))
    return docs


# ---------------------------------------------------------------- machine CSV
def render_machine(cfg: Config, station: str, recs: list[Record]) -> list[DocFile]:
    docs = []
    for day, group in group_by(recs, lambda r: r.measured_at.date()).items():
        group.sort(key=lambda r: (r.measured_at, r.record_id))
        cols = ["lot", "panel", "machine", "item", "value", "time"]
        headers = {
            "lot": "lot_id",
            "panel": "panel_no",
            "machine": "equipment_id",
            "item": "inspection_item",
            "value": "value",
            "time": "timestamp",
        }
        rows, item_of_row = [], {}
        for r in group:
            row = Row(
                "data",
                {
                    "lot": r.lot_no,
                    "panel": str(r.panel),
                    "machine": r.machine,
                    "item": r.item,
                    "value": str(r.value),
                    "time": str(epoch(r.measured_at)),
                },
                {"value": r.record_id},
            )
            rows.append(row)
            item_of_row[id(row)] = r.item
        name = f"{MACHINE_PREFIX.get(station, 'EQP')}_export_{station}_{day:%Y%m%d}.csv"
        docs.append(
            DocFile(
                filename=name,
                source="SRC_MACHINE",
                station=station,
                fmt="csv",
                encoding="utf-8",
                sheet=Sheet("export", cols, headers, rows),
                roles=Roles(
                    lot="lot",
                    machine="machine",
                    item="item",
                    values=["value"],
                    time="time",
                    time_style="epoch",
                    required=["lot", "machine", "item", "value", "time"],
                ),
                item_of_row=item_of_row,
            )
        )
    return docs


# ---------------------------------------------------------- station wide xlsx
def render_station(cfg: Config, station: str, recs: list[Record]) -> list[DocFile]:
    st = cfg.station(station)
    points = cfg.scenario["points_per_panel"]
    pcols = [f"P{i}" for i in range(1, points + 1)]
    docs = []
    # Build wide groups first, then file them by the shift of their first point,
    # so one (lot, panel, item) row is never split across two daily files.
    wide_groups = group_by(recs, lambda r: (r.lot_no, r.panel, r.item)).values()
    by_file: dict[tuple, list[Record]] = {}
    for g in wide_groups:
        first_ts = min(r.measured_at for r in g)
        shift = "日班" if 8 <= first_ts.hour < 20 else "夜班"
        by_file.setdefault((shift_day(first_ts), shift), []).extend(g)
    for (day, shift), group in sorted(by_file.items(), key=lambda kv: str(kv[0])):
        cols = ["time", "shift", "lot", "panel", "machine", "item", "unit", *pcols, "operator"]
        headers = {
            "time": "量測時間",
            "shift": "班別",
            "lot": "批號",
            "panel": "片號",
            "machine": "機台",
            "item": "檢驗項目",
            "unit": "單位",
            "operator": "作業員",
            **{p: p for p in pcols},
        }
        rows, item_of_row = [], {}
        wide = group_by(group, lambda r: (r.lot_no, r.panel, r.item))
        ordered = sorted(wide.values(), key=lambda g: (min(r.measured_at for r in g), g[0].record_id))
        for g in ordered:
            g.sort(key=lambda r: r.point or 0)
            first = g[0]
            item = cfg.item(first.item)
            cells = {
                "time": min(r.measured_at for r in g),
                "shift": shift,
                "lot": first.lot_no,
                "panel": first.panel,
                "machine": first.machine,
                "item": item.name,
                "unit": item.unit,
                "operator": first.operator,
            }
            ids = {}
            for r in g:
                key = f"P{r.point}"
                cells[key] = r.value
                ids[key] = r.record_id
            row = Row("data", cells, ids)
            rows.append(row)
            item_of_row[id(row)] = first.item
        name = f"{st.name}站_日報_{day:%Y%m%d}_{shift}.xlsx"
        docs.append(
            DocFile(
                filename=name,
                source="SRC_STATION",
                station=station,
                fmt="xlsx",
                encoding="",
                sheet=Sheet("日報", cols, headers, rows),
                roles=Roles(
                    lot="lot",
                    machine="machine",
                    item="item",
                    values=pcols,
                    time="time",
                    time_style="excel",
                    unit="unit",
                    required=["time", "lot", "panel", "machine", "item", "unit"],
                ),
                item_of_row=item_of_row,
            )
        )
    return docs


# --------------------------------------------------------- manual record xlsx
def render_manual(cfg: Config, station: str, recs: list[Record]) -> list[DocFile]:
    st = cfg.station(station)
    company = cfg.scenario["company"]
    docs = []
    for day, group in group_by(recs, lambda r: r.measured_at.date()).items():
        group.sort(key=lambda r: (r.measured_at, r.record_id))
        cols = ["time", "lot", "panel", "point", "machine", "item", "value", "unit", "note"]
        headers = {
            "time": "量測時間",
            "lot": "批號",
            "panel": "片號",
            "point": "量測點",
            "machine": "機台",
            "item": "項目",
            "value": "數值",
            "unit": "單位",
            "note": "備註",
        }
        rows, item_of_row = [], {}
        for r in group:
            item = cfg.item(r.item)
            row = Row(
                "data",
                {
                    "time": r.measured_at,
                    "lot": r.lot_no,
                    "panel": r.panel,
                    "point": r.point,
                    "machine": r.machine,
                    "item": item.name,
                    "value": r.value,
                    "unit": item.unit,
                    "note": None,
                },
                {"value": r.record_id},
            )
            rows.append(row)
            item_of_row[id(row)] = r.item
        rows.append(Row("total", {"time": "合計", "value": f"{len(group)} 筆"}))
        filler = group[0].operator
        preamble = [
            [f"{company} {st.name} 量測紀錄表"],
            [f"填表人：{filler}", None, None, None, f"日期：{day:%Y/%m/%d}"],
            [],
        ]
        name = f"{st.name}_量測紀錄表_{day:%Y%m%d}.xlsx"
        docs.append(
            DocFile(
                filename=name,
                source="SRC_MANUAL",
                station=station,
                fmt="xlsx",
                encoding="",
                sheet=Sheet("紀錄", cols, headers, rows, preamble, ["A1:I1"]),
                roles=Roles(
                    lot="lot",
                    machine="machine",
                    item="item",
                    values=["value"],
                    time="time",
                    time_style="excel",
                    unit="unit",
                    required=["time", "lot", "panel", "machine", "item", "value", "unit"],
                ),
                item_of_row=item_of_row,
            )
        )
    return docs


# ------------------------------------------------------------- legacy Big5 CSV
def render_legacy(cfg: Config, station: str, recs: list[Record]) -> list[DocFile]:
    docs = []
    for day, group in group_by(recs, lambda r: r.measured_at.date()).items():
        group.sort(key=lambda r: (r.measured_at, r.record_id))
        cols = ["lot", "panel", "point", "machine", "item", "value", "unit", "date", "clock", "operator"]
        headers = {
            "lot": "LOTNO",
            "panel": "PNL",
            "point": "PT",
            "machine": "EQP",
            "item": "ITEM",
            "value": "VAL",
            "unit": "UOM",
            "date": "DT",
            "clock": "TM",
            "operator": "OP",
        }
        rows, item_of_row = [], {}
        for r in group:
            item = cfg.item(r.item)
            row = Row(
                "data",
                {
                    "lot": r.lot_no,
                    "panel": str(r.panel),
                    "point": str(r.point),
                    "machine": r.machine,
                    "item": r.item,
                    "value": fmt_number(r.value, item.decimals),
                    "unit": "UM",
                    "date": roc_date(r.measured_at),
                    "clock": r.measured_at.strftime("%H%M%S"),
                    "operator": r.operator,
                },
                {"value": r.record_id},
            )
            rows.append(row)
            item_of_row[id(row)] = r.item
        name = f"MES_OLD_{station}_{roc_date(day)}.csv"
        docs.append(
            DocFile(
                filename=name,
                source="SRC_LEGACY",
                station=station,
                fmt="csv",
                encoding="cp950",
                sheet=Sheet("export", cols, headers, rows),
                roles=Roles(
                    lot="lot",
                    machine="machine",
                    item="item",
                    values=["value"],
                    time=None,
                    time_style="legacy",
                    unit="unit",
                    required=["lot", "machine", "item", "value", "date", "clock"],
                ),
                item_of_row=item_of_row,
            )
        )
    return docs
