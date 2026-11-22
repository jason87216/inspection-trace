"""Preview and initialize master data from the generator's truth CSVs."""

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from inspection_trace.db import Base, get_engine
from inspection_trace.ingestion.errors import (
    E_SEED_CONFLICT,
    E_SEED_DATABASE,
    E_SEED_INPUT,
    E_SEED_STALE,
)
from inspection_trace.models import (
    InspectionItem,
    Lot,
    LotLink,
    LotMove,
    Machine,
    Part,
    QTimeRule,
    Station,
)

type SeedStatus = Literal["new", "unchanged", "changed", "database_only"]
type SeedKey = tuple[str, ...]
type SeedValue = str | int | float | datetime | None
type SeedRow = dict[str, SeedValue]
type CsvColumn = str | tuple[str, Callable[[str], SeedValue]]

TAIPEI = ZoneInfo("Asia/Taipei")
MACHINE_VALID_FROM = datetime(1970, 1, 1, tzinfo=UTC)
# Standard simulated laser-to-plating rule from generator/config/scenario.toml.
DEFAULT_QTIME_STATIONS = ("B20", "B60")
DEFAULT_QTIME_LIMIT_HOURS = 8.0


class SeedError(ValueError):
    """A seed failure with a stable code for callers and tests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SeedTable:
    model: type[Base]
    keys: tuple[str, ...]
    columns: tuple[str, ...]
    editable: tuple[str, ...]
    rows: dict[SeedKey, SeedRow]


@dataclass(frozen=True)
class SeedEntry:
    table: str
    key: SeedKey
    status: SeedStatus | Literal["blocked"]
    differences: tuple[tuple[str, SeedValue, SeedValue], ...] = ()


def _classify[K, V](source: dict[K, V], existing: dict[K, V]) -> dict[K, SeedStatus]:
    result: dict[K, SeedStatus] = {}
    for key, row in source.items():
        if key not in existing:
            result[key] = "new"
        else:
            result[key] = "unchanged" if row == existing[key] else "changed"
    for key in existing.keys() - source.keys():
        result[key] = "database_only"
    return result


def compare_stations(
    source: Sequence[Station], existing: Sequence[Station]
) -> dict[str, SeedStatus]:
    """Classify stations by code using already parsed and loaded rows.

    Each input contains unique station codes. A source-only code is `new`, and
    a database-only code is `database_only`. For a code in both inputs, a
    difference in name or sequence is `changed`. Otherwise it is `unchanged`.
    The active flag is owned by the database and is ignored.

    Return one status per code without changing the input rows, reading files,
    or querying the database.
    """
    return _classify(
        {station.code: (station.name, station.seq) for station in source},
        {station.code: (station.name, station.seq) for station in existing},
    )


def _taipei_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed.astimezone(TAIPEI)


def _optional_time(value: str) -> datetime | None:
    return _taipei_time(value) if value else None


def _optional_float(value: str) -> float | None:
    return _finite_float(value) if value else None


def _finite_float(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError("specification values must be finite")
    return number


def _table(
    root: Path,
    model: type[Base],
    keys: tuple[str, ...],
    inputs: dict[str, dict[str, CsvColumn]],
    *,
    editable: tuple[str, ...] = (),
) -> SeedTable:
    rows: dict[SeedKey, SeedRow] = {}
    columns = tuple(dict.fromkeys(column for fields in inputs.values() for column in fields))
    if model is Lot:
        columns += ("lot_type",)
    for filename, fields in inputs.items():
        with (root / filename).open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file, strict=True)
            required = {rule if isinstance(rule, str) else rule[0] for rule in fields.values()}
            headers = reader.fieldnames or []
            if not required.issubset(headers) or len(headers) != len(set(headers)):
                raise SeedError(E_SEED_INPUT, f"{filename}: missing or duplicate CSV columns")
            for raw in reader:
                try:
                    if None in raw or None in raw.values():
                        raise ValueError("wrong number of columns")
                    row: SeedRow = dict.fromkeys(columns)
                    for field_name, rule in fields.items():
                        if isinstance(rule, str):
                            row[field_name] = raw[rule]
                        else:
                            source_column, convert = rule
                            row[field_name] = convert(raw[source_column])
                    if model is Lot:
                        row["lot_type"] = (
                            "material" if filename == "materials.csv" else "production"
                        )
                    key = tuple(str(row[name]) for name in keys)
                    if any(not value.strip() for value in key) or key in rows:
                        raise ValueError("empty or duplicate key")
                    rows[key] = row
                except ValueError as exc:
                    raise SeedError(E_SEED_INPUT, f"{filename}:{reader.line_num}: {exc}") from exc
    return SeedTable(model, keys, columns, editable, rows)


def read_seed_tables(root: Path) -> tuple[SeedTable, ...]:
    """Parse the generator CSVs and add the default rule if its stations are present."""
    stations = _table(
        root,
        Station,
        ("code",),
        {"stations.csv": {"code": "code", "name": "name", "seq": ("seq", int)}},
        editable=("name", "seq"),
    )
    tables = (
        stations,
        _table(
            root,
            Part,
            ("part_no",),
            {"parts.csv": {"part_no": "part_no", "name": "name"}},
            editable=("name",),
        ),
        _table(
            root, Machine, ("code",), {"machines.csv": {"code": "code", "station_code": "station"}}
        ),
        _table(
            root,
            InspectionItem,
            ("code",),
            {
                "items.csv": {
                    "code": "code",
                    "name": "name",
                    "station_code": "station",
                    "kind": "kind",
                    "unit": "unit",
                    "lsl": ("lsl", _optional_float),
                    "target": ("target", _optional_float),
                    "usl": ("usl", _finite_float),
                    "physical_min": ("physical_min", _finite_float),
                }
            },
            editable=("name",),
        ),
        _table(
            root,
            Lot,
            ("lot_no",),
            {
                "materials.csv": {"lot_no": "lot_no", "kind": "kind"},
                "lots.csv": {
                    "lot_no": "lot_no",
                    "part_no": "part_no",
                    "panel_count": ("panel_count", int),
                    "released_at": ("release", _taipei_time),
                },
            },
        ),
        _table(
            root,
            LotLink,
            ("parent_lot_no", "child_lot_no"),
            {
                "lot_links.csv": {
                    "parent_lot_no": "parent",
                    "child_lot_no": "child",
                    "relation": "relation",
                }
            },
        ),
        _table(
            root,
            LotMove,
            ("lot_no", "station_code"),
            {
                "lot_moves.csv": {
                    "lot_no": "lot_no",
                    "station_code": "station",
                    "machine_code": "machine",
                    "start_at": ("start", _taipei_time),
                    "end_at": ("end", _optional_time),
                    "operator": "operator",
                }
            },
        ),
    )
    keys = ("from_station_code", "to_station_code")
    if (root / "qtime_rules.csv").is_file():
        rules = _table(
            root,
            QTimeRule,
            keys,
            {
                "qtime_rules.csv": {
                    **{key: key for key in keys},
                    "limit_hours": ("limit_hours", _finite_float),
                }
            },
        )
        return (*tables, rules)
    start, end = DEFAULT_QTIME_STATIONS
    if (start,) not in stations.rows or (end,) not in stations.rows:
        return tables
    return (
        *tables,
        SeedTable(
            QTimeRule,
            keys,
            (*keys, "limit_hours"),
            (),
            {
                DEFAULT_QTIME_STATIONS: {
                    "from_station_code": start,
                    "to_station_code": end,
                    "limit_hours": DEFAULT_QTIME_LIMIT_HOURS,
                }
            },
        ),
    )


def plan_seed(session: Session, tables: Sequence[SeedTable]) -> list[SeedEntry]:
    """Read all managed rows and classify differences without writing."""
    plan: list[SeedEntry] = []
    with session.no_autoflush:
        for table in tables:
            fields = [getattr(table.model, name) for name in table.columns]
            snapshots = {
                tuple(row[name] for name in table.keys): dict(row)
                for row in session.execute(select(*fields)).mappings()
            }
            statuses = _classify(table.rows, snapshots)
            for key, status in sorted(statuses.items()):
                differences: tuple[tuple[str, SeedValue, SeedValue], ...] = ()
                if status == "changed":
                    differences = tuple(
                        (name, snapshots[key][name], desired)
                        for name, desired in table.rows[key].items()
                        if snapshots[key][name] != desired
                    )
                blocked = any(name not in table.editable for name, _, _ in differences)
                plan.append(
                    SeedEntry(
                        table.model.__tablename__,
                        key,
                        "blocked" if blocked else status,
                        differences,
                    )
                )
    return plan


def _validate_lot_links(session: Session, tables: Sequence[SeedTable]) -> None:
    lots = next(table.rows for table in tables if table.model is Lot)
    links = next(table.rows for table in tables if table.model is LotLink)
    lot_types: dict[str, SeedValue] = {
        code: kind for code, kind in session.execute(select(Lot.lot_no, Lot.lot_type))
    }
    lot_types.update({key[0]: row["lot_type"] for key, row in lots.items()})
    relations = {"consume": ("material", "production"), "split": ("production", "production")}
    for (parent, child), row in links.items():
        relation = row["relation"]
        if parent == child or relations.get(relation) != (
            lot_types.get(parent),
            lot_types.get(child),
        ):
            raise SeedError(E_SEED_INPUT, f"Invalid lot link {parent} → {child} ({relation})")


def apply_seed(
    session: Session, tables: Sequence[SeedTable], *, expected: Sequence[SeedEntry] | None = None
) -> None:
    """Recheck under a write lock and apply all approved changes in one transaction."""
    with session.begin():
        session.execute(
            text(
                "LOCK TABLE station, part, machine, inspection_item, lot, lot_link, lot_move, "
                "qtime_rule IN SHARE ROW EXCLUSIVE MODE"
            )
        )
        plan = plan_seed(session, tables)
        if expected is not None and plan != list(expected):
            raise SeedError(E_SEED_STALE, "The database changed after preview; run seed again.")
        if any(entry.status == "blocked" for entry in plan):
            raise SeedError(E_SEED_CONFLICT, "Protected master-data fields have changed.")
        _validate_lot_links(session, tables)
        entries_by_table: dict[str, list[SeedEntry]] = {}
        for entry in plan:
            entries_by_table.setdefault(entry.table, []).append(entry)
        for table in tables:
            for entry in entries_by_table.get(table.model.__tablename__, []):
                if entry.status == "new":
                    values = table.rows[entry.key].copy()
                    if table.model is Machine:
                        values["valid_from"] = MACHINE_VALID_FROM
                    session.add(table.model(**values))
                elif entry.status == "changed":
                    predicates = [
                        getattr(table.model, name) == value
                        for name, value in zip(table.keys, entry.key, strict=True)
                    ]
                    changes = {name: desired for name, _, desired in entry.differences}
                    session.execute(update(table.model).where(*predicates).values(**changes))
            session.flush()


def _print_plan(plan: Sequence[SeedEntry]) -> None:
    counts = Counter(entry.status for entry in plan)
    for entry in plan:
        if entry.status == "unchanged":
            continue
        print(f"{entry.table} {', '.join(entry.key)}: {entry.status}")
        for name, before, after in entry.differences:
            print(f"  {name}: {before!r} -> {after!r}")
    print(
        "Summary:",
        ", ".join(
            f"{status}={counts[status]}"
            for status in ("new", "changed", "blocked", "database_only", "unchanged")
        ),
    )


def main() -> int:
    """Run the seed from the current project directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-dir", type=Path, default=Path("samples/truth"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Preview differences only.")
    mode.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")
    args = parser.parse_args()
    try:
        tables = read_seed_tables(args.truth_dir)
        with Session(get_engine()) as session:
            with session.begin():
                plan = plan_seed(session, tables)
            _print_plan(plan)
            if any(entry.status == "blocked" for entry in plan):
                raise SeedError(E_SEED_CONFLICT, "Protected master-data fields have changed.")
            if args.check or not any(entry.status in {"new", "changed"} for entry in plan):
                return 0
            confirmed = args.yes or (
                sys.stdin.isatty()
                and input("Apply changes? [y/N] ").strip().lower() in {"y", "yes"}
            )
            if confirmed:
                apply_seed(session, tables, expected=plan)
                print("Seed applied.")
            else:
                print("No changes applied.")
            return 0
    except SeedError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
    except (OSError, csv.Error, UnicodeError, EOFError) as exc:
        print(f"{E_SEED_INPUT}: {exc}", file=sys.stderr)
    except (SQLAlchemyError, ValidationError):
        print(
            f"{E_SEED_DATABASE}: Check the database connection, schema and constraints.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
