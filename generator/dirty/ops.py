"""Dirty operations. Each one changes a document and returns Injection records.

Categories
  A  should be normalized and accepted (alias, unit, date format, full-width...)
  B  should be rejected with a fixed error code
  C  cannot be detected by validation by design (plausible typo, wrong machine)

Error codes match the ingestion side (src/inspection_trace/ingestion/errors.py).
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from generator.config import Config
from generator.render.common import TW
from generator.table import DocFile, Row

MIL_PER_UM = 1 / 25.4

E_REQUIRED_MISSING = "E_REQUIRED_MISSING"
E_NOT_NUMERIC = "E_NOT_NUMERIC"
E_UNIT_UNKNOWN = "E_UNIT_UNKNOWN"
E_LOT_FORMAT = "E_LOT_FORMAT"
E_REF_NOT_FOUND = "E_REF_NOT_FOUND"
E_OUT_OF_PHYSICAL_RANGE = "E_OUT_OF_PHYSICAL_RANGE"
E_DUPLICATE_IN_FILE = "E_DUPLICATE_IN_FILE"

HEADER_ALIASES: dict[str, dict[str, list[str]]] = {
    "SRC_MACHINE": {
        "lot": ["LotID", "LOT_NO", "lot no"],
        "machine": ["eqp_id", "machine", "EQP"],
        "value": ["result", "count"],
        "time": ["ts", "time"],
    },
    "SRC_STATION": {
        "lot": ["Lot", "LOT NO", "批次"],
        "machine": ["設備", "機台編號"],
        "item": ["項目", "量測項目"],
        "time": ["日期時間", "時間"],
        "panel": ["Panel", "片"],
    },
    "SRC_MANUAL": {
        "lot": ["批次號碼", "Lot No."],
        "value": ["量測值", "讀值"],
        "machine": ["設備"],
        "point": ["點位", "Point"],
    },
    "SRC_LEGACY": {
        "lot": ["LOT_NO", "LOT"],
        "machine": ["EQPID", "MC"],
        "value": ["VALUE", "DATA"],
    },
}

FULLWIDTH = str.maketrans("0123456789.-", "０１２３４５６７８９．－")
NOT_NUMERIC = ["N/A", "1.2.3", "—", "OK", "量不到", "#VALUE!"]
UNIT_ALIASES = ["um", "µm", "UM", "微米"]  # note: "µ" is MICRO SIGN U+00B5
UNKNOWN_UNITS = ["um?", "公分?", "x", "mm/um"]


@dataclass
class Injection:
    op: str
    category: str  # "A" | "B" | "C"
    expected_error_code: str | None
    row: Row | None  # None means the header row
    column: str | None  # column key; resolved to final header text on output
    original_value: Any
    dirty_value: Any
    expected_normalized_value: Any = None
    tolerance: float | None = None
    note: str | None = None


def _json_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


class Dirtier:
    def __init__(self, cfg: Config, machines: dict[str, list[str]], rng: random.Random) -> None:
        self.cfg = cfg
        self.machines = machines
        self.rng = rng
        self.dcfg = cfg.dirty

    # ---------------------------------------------------------------- entry
    def apply(self, doc: DocFile) -> list[Injection]:
        injections: list[Injection] = []
        source = doc.source
        rate = self.dcfg["row_rates"][source]
        weights = self.dcfg["ops"][source]
        data_rows = [r for r in doc.sheet.rows if r.kind == "data"]

        dup_targets: list[Row] = []
        for row in data_rows:
            if self.rng.random() >= rate:
                continue
            self._one_cell_op(doc, row, weights, injections, dup_targets)

        if "kinds" not in self.cfg.materials and not injections and not dup_targets and data_rows:
            # a dirty file must actually be dirty
            for _ in range(10):
                row = self.rng.choice(data_rows)
                self._one_cell_op(doc, row, weights, injections, dup_targets)
                if injections or dup_targets:
                    break

        for row in dup_targets:
            injections.append(self._dup_row(doc, row))

        fops = self.dcfg["file_ops"]
        if source == "SRC_MANUAL" and self.rng.random() < fops["D_LAYOUT_NOISE"]:
            injections.extend(self._layout_noise(doc))
        if self.rng.random() < fops["D_HEADER_ALIAS"]:
            injections.extend(self._header_alias(doc))
        return injections

    def _one_cell_op(
        self,
        doc: DocFile,
        row: Row,
        weights: dict[str, int],
        injections: list[Injection],
        dup_targets: list[Row],
    ) -> None:
        if row.dirtied:
            return
        ops = [name for name, weight in weights.items() if weight > 0]
        if not ops:
            return
        op = self.rng.choices(ops, weights=[weights[o] for o in ops])[0]
        if op == "D_DUP_ROW":
            row.dirtied = True
            dup_targets.append(row)
            return
        result = getattr(self, "_" + op.lower()[2:])(doc, row)
        if result:
            row.dirtied = True
            injections.extend(result)

    # ------------------------------------------------------------- helpers
    def _item(self, doc: DocFile, row: Row):
        return self.cfg.item(doc.item_code(row))

    def _value_key(self, doc: DocFile, row: Row) -> str:
        return self.rng.choice([k for k in doc.roles.values if k in row.record_ids])

    @staticmethod
    def _as_float(v: Any) -> float:
        return float(v)

    def _set_number(self, doc: DocFile, row: Row, key: str, value: float, decimals: int) -> None:
        """Keep the cell type of the source: strings for CSV, numbers for xlsx."""
        if doc.fmt == "csv":
            row.cells[key] = f"{value:.{decimals}f}"
        else:
            row.cells[key] = value

    # ---------------------------------------------------------- category A
    def _unit_mil(self, doc: DocFile, row: Row) -> list[Injection]:
        unit_key = doc.roles.unit
        item = self._item(doc, row)
        if unit_key is None or item.unit != "μm":
            return []
        mil_label = "MIL" if doc.source == "SRC_LEGACY" else "mil"
        out = []
        original_unit = row.cells[unit_key]
        row.cells[unit_key] = mil_label
        for key in doc.roles.values:
            if key not in row.record_ids:
                continue
            original = self._as_float(row.cells[key])
            mil = round(original * MIL_PER_UM, 5)
            self._set_number(doc, row, key, mil, 5)
            out.append(
                Injection(
                    "D_UNIT_MIL", "A", None, row, key, original, row.cells[key],
                    expected_normalized_value=original, tolerance=0.001,
                    note=f"unit {original_unit} -> {mil_label}; 1 mil = 25.4 μm",
                )
            )
        return out

    def _unit_alias(self, doc: DocFile, row: Row) -> list[Injection]:
        unit_key = doc.roles.unit
        item = self._item(doc, row)
        if unit_key is None or item.unit != "μm" or doc.source == "SRC_LEGACY":
            return []
        alias = self.rng.choice(UNIT_ALIASES)
        row.cells[unit_key] = alias
        return [
            Injection("D_UNIT_ALIAS", "A", None, row, unit_key, "μm", alias,
                      expected_normalized_value="μm")
        ]

    def _date_format(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.time
        if key is None:
            return []
        original = row.cells[key]
        if doc.roles.time_style == "epoch":
            ts = datetime.fromtimestamp(int(original), TW).replace(tzinfo=None)
            choices = [
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                ts.strftime("%Y/%m/%d %H:%M"),
                ts.strftime("%Y%m%d%H%M%S"),
            ]
            dirty = self.rng.choice(choices)
        else:
            ts = original
            serial = (ts - datetime(1899, 12, 30)).total_seconds() / 86400
            choices = [
                ts.strftime("%Y/%m/%d %H:%M"),
                ts.strftime("%Y%m%d %H%M"),
                round(serial, 6),
                f"{ts.year - 1911:03d}/{ts:%m/%d %H:%M}",
            ]
            dirty = self.rng.choice(choices)
        row.cells[key] = dirty
        return [
            Injection(
                "D_DATE_FORMAT", "A", None, row, key, _json_value(ts), dirty,
                expected_normalized_value=ts.isoformat(), tolerance=60,
                note="tolerance in seconds",
            )
        ]

    def _fullwidth(self, doc: DocFile, row: Row) -> list[Injection]:
        key = self._value_key(doc, row)
        item = self._item(doc, row)
        original = row.cells[key]
        text = original if isinstance(original, str) else (
            str(original) if item.kind == "count" else f"{original:.{item.decimals}f}"
        )
        dirty = text.translate(FULLWIDTH)
        row.cells[key] = dirty
        return [
            Injection("D_FULLWIDTH", "A", None, row, key, original, dirty,
                      expected_normalized_value=self._as_float(original), tolerance=0.0)
        ]

    def _whitespace(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.lot
        lot = row.cells[key]
        if self.rng.random() < 0.5:
            dirty = f"  {lot} "
        else:
            cut = self.rng.randint(4, len(lot) - 3)
            dirty = f"{lot[:cut]} {lot[cut:]}"
        row.cells[key] = dirty
        return [
            Injection("D_WHITESPACE", "A", None, row, key, lot, dirty,
                      expected_normalized_value=lot)
        ]

    # ---------------------------------------------------------- category B
    def _required_blank(self, doc: DocFile, row: Row) -> list[Injection]:
        candidates = [k for k in doc.roles.required if row.cells.get(k) not in (None, "")]
        candidates += [k for k in doc.roles.values if k in row.record_ids]
        key = self.rng.choice(candidates)
        original = row.cells[key]
        row.cells[key] = "" if doc.fmt == "csv" else None
        return [
            Injection("D_REQUIRED_BLANK", "B", E_REQUIRED_MISSING, row, key,
                      _json_value(original), None)
        ]

    def _not_numeric(self, doc: DocFile, row: Row) -> list[Injection]:
        key = self._value_key(doc, row)
        original = row.cells[key]
        dirty = self.rng.choice(NOT_NUMERIC)
        row.cells[key] = dirty
        return [Injection("D_NOT_NUMERIC", "B", E_NOT_NUMERIC, row, key, original, dirty)]

    def _unit_unknown(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.unit
        if key is None:
            return []
        original = row.cells[key]
        dirty = self.rng.choice(UNKNOWN_UNITS)
        row.cells[key] = dirty
        return [Injection("D_UNIT_UNKNOWN", "B", E_UNIT_UNKNOWN, row, key, original, dirty)]

    def _lot_typo(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.lot
        lot = row.cells[key]
        base, suffix = (lot.split("-", 1) + [""])[:2]
        suffix = f"-{suffix}" if suffix else ""
        kind = self.rng.choice(["drop", "extra", "letter"])
        if kind == "drop":
            i = self.rng.randint(1, len(base) - 1)
            dirty = base[:i] + base[i + 1 :] + suffix
        elif kind == "extra":
            i = self.rng.randint(1, len(base) - 1)
            dirty = base[:i] + str(self.rng.randint(0, 9)) + base[i:] + suffix
        else:
            digits = [i for i, ch in enumerate(base) if ch in "01" and i > 0]
            if not digits:
                i = self.rng.randint(1, len(base) - 1)
                dirty = base[:i] + base[i + 1 :] + suffix
            else:
                i = self.rng.choice(digits)
                dirty = base[:i] + {"0": "O", "1": "I"}[base[i]] + base[i + 1 :] + suffix
        row.cells[key] = dirty
        return [Injection("D_LOT_TYPO", "B", E_LOT_FORMAT, row, key, lot, dirty, note=kind)]

    def _ref_unknown(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.machine
        original = row.cells[key]
        prefix = original.split("-")[0]
        dirty = f"{prefix}-{self.rng.randint(90, 99)}"
        row.cells[key] = dirty
        return [Injection("D_REF_UNKNOWN", "B", E_REF_NOT_FOUND, row, key, original, dirty)]

    def _physical_impossible(self, doc: DocFile, row: Row) -> list[Injection]:
        key = self._value_key(doc, row)
        item = self._item(doc, row)
        original = row.cells[key]
        v = self._as_float(original)
        if item.kind == "count":
            dirty_num = -float(self.rng.randint(1, 5))
            dirty = str(int(dirty_num)) if doc.fmt == "csv" else int(dirty_num)
        else:
            dirty_num = -abs(v) if v != 0 else -1.0
            self._set_number(doc, row, key, dirty_num, item.decimals)
            dirty = row.cells[key]
        row.cells[key] = dirty
        return [
            Injection("D_PHYSICAL_IMPOSSIBLE", "B", E_OUT_OF_PHYSICAL_RANGE, row, key,
                      original, dirty)
        ]

    def _dup_row(self, doc: DocFile, row: Row) -> Injection:
        clone = Row(row.kind, copy.deepcopy(row.cells), dict(row.record_ids), dirtied=True)
        rows = doc.sheet.rows
        rows.insert(rows.index(row) + 1, clone)
        doc.item_of_row[id(clone)] = doc.item_code(row)
        return Injection("D_DUP_ROW", "B", E_DUPLICATE_IN_FILE, clone, None, None, None,
                         note="exact copy of the previous row")

    # ---------------------------------------------------------- category C
    def _transpose(self, doc: DocFile, row: Row) -> list[Injection]:
        key = self._value_key(doc, row)
        item = self._item(doc, row)
        if item.kind != "continuous":
            return []
        original = self._as_float(row.cells[key])
        text = f"{original:.{item.decimals}f}"
        lo = item.lsl if item.lsl is not None else item.physical_min
        positions = [i for i in range(len(text) - 1) if text[i].isdigit() and text[i + 1].isdigit()]
        self.rng.shuffle(positions)
        for i in positions:
            swapped = text[:i] + text[i + 1] + text[i] + text[i + 2 :]
            new = float(swapped)
            if new != original and lo <= new <= item.usl:
                self._set_number(doc, row, key, new, item.decimals)
                return [
                    Injection("D_TRANSPOSE", "C", None, row, key, original, row.cells[key],
                              expected_normalized_value=new,
                              note="plausible digit swap; validation cannot catch it")
                ]
        return []

    def _wrong_machine(self, doc: DocFile, row: Row) -> list[Injection]:
        key = doc.roles.machine
        original = row.cells[key]
        others = [m for m in self.machines[doc.station] if m != original]
        if not others:
            return []
        dirty = self.rng.choice(others)
        row.cells[key] = dirty
        return [
            Injection("D_WRONG_MACHINE", "C", None, row, key, original, dirty,
                      expected_normalized_value=dirty,
                      note="existing machine of the same station; validation cannot catch it")
        ]

    # ----------------------------------------------------------- file level
    def _layout_noise(self, doc: DocFile) -> list[Injection]:
        rows = doc.sheet.rows
        data_positions = [i for i, r in enumerate(rows) if r.kind == "data"]
        if len(data_positions) < 4:
            return []
        out = []
        for kind in self.rng.sample(["blank", "blank", "total"], k=self.rng.randint(1, 3)):
            pos = self.rng.randint(1, len(rows) - 2)
            if kind == "blank":
                new = Row("blank", {})
            else:
                new = Row("total", {"time": "小計", "value": f"{self.rng.randint(5, 40)} 筆"})
            rows.insert(pos, new)
            out.append(
                Injection("D_LAYOUT_NOISE", "A", None, new, None, None, kind,
                          expected_normalized_value="ROW_IGNORED")
            )
        return out

    def _header_alias(self, doc: DocFile) -> list[Injection]:
        aliases = HEADER_ALIASES[doc.source]
        keys = [k for k in aliases if k in doc.sheet.headers]
        out = []
        for key in self.rng.sample(keys, k=min(len(keys), self.rng.randint(1, 2))):
            original = doc.sheet.headers[key]
            alias = self.rng.choice(aliases[key])
            doc.sheet.headers[key] = alias
            out.append(
                Injection("D_HEADER_ALIAS", "A", None, None, key, original, alias,
                          expected_normalized_value=original)
            )
        return out
