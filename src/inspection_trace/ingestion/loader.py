"""Write a validated file in one transaction; re-uploading the same file is a no-op."""

import csv
from hashlib import sha256
from itertools import batched
from pathlib import Path

from sqlalchemy import func, insert, or_, select, text, tuple_
from sqlalchemy.orm import Session

from inspection_trace.ingestion.errors import (
    E_DUPLICATE_IN_FILE,
    E_FILE_FORMAT,
    E_MEASUREMENT_CONFLICT,
)
from inspection_trace.ingestion.lab_pdf import map_lab_pdf
from inspection_trace.ingestion.mapping import HeaderMappingError, map_import_file
from inspection_trace.ingestion.normalization import clean_text
from inspection_trace.ingestion.readers import FileReadError
from inspection_trace.ingestion.templates import TemplateRules, map_template_file
from inspection_trace.ingestion.validation import (
    ImportIssue,
    MasterData,
    Measurement,
    MeasurementKey,
    validate_row,
)
from inspection_trace.models import (
    ImportBatch,
    ImportErrorDetail,
    ImportTemplate,
    InspectionItem,
    InspectionRecord,
    Lot,
    LotMove,
    Machine,
    Part,
    Station,
)

ACCEPT = "ACCEPT"
REJECT = "REJECT"
DUPLICATE_FILE = "DUPLICATE_FILE"
OPERATORS_PATH = Path("samples/truth/operators.csv")
MAX_MEASUREMENTS = 30_000
MAX_PROVENANCE_DETAILS = 5


def _master_data(session: Session, operators_path: Path) -> MasterData:
    with operators_path.open(encoding="utf-8-sig", newline="") as stream:
        operators = {row["code"] for row in csv.DictReader(stream)}
    items = list(session.scalars(select(InspectionItem)))
    return MasterData(
        lots={row.lot_no: row for row in session.scalars(select(Lot))},
        active_parts=set(session.scalars(select(Part.part_no).where(Part.active))),
        active_stations=set(session.scalars(select(Station.code).where(Station.active))),
        machines={row.code: row for row in session.scalars(select(Machine))},
        items={row.code: row for row in items},
        items_by_name={(row.station_code, clean_text(row.name)): row for row in items},
        moves={(row.lot_no, row.station_code): row for row in session.scalars(select(LotMove))},
        operators=operators,
    )


def _existing_records(
    session: Session, measurements: dict[MeasurementKey, Measurement]
) -> dict[MeasurementKey, InspectionRecord]:
    fields = (
        InspectionRecord.lot_no,
        InspectionRecord.station_code,
        InspectionRecord.item_code,
        InspectionRecord.panel,
        func.coalesce(InspectionRecord.point, 0),
    )
    existing: dict[MeasurementKey, InspectionRecord] = {}
    for group in batched(measurements, 1000):
        keys = [(*key[:4], key[4] or 0) for key in group]
        for row in session.scalars(select(InspectionRecord).where(tuple_(*fields).in_(keys))):
            existing[(row.lot_no, row.station_code, row.item_code, row.panel, row.point)] = row
    return existing


def _provenance_warnings(
    measurements: dict[MeasurementKey, Measurement],
    existing: dict[MeasurementKey, InspectionRecord],
    moves: dict[tuple[str, str], LotMove],
) -> list[str]:
    changed_count = 0
    changed_examples: list[str] = []
    move_rows: set[int] = set()
    move_examples: list[str] = []
    for key, row in measurements.items():
        stored = existing.get(key)
        if stored is not None:
            changed_fields = [
                label
                for label, incoming, original in (
                    ("機台", row.machine_code, stored.machine_code),
                    ("量測時間", row.measured_at, stored.measured_at),
                    ("操作員", row.operator, stored.operator),
                )
                if incoming != original
            ]
            if changed_fields:
                changed_count += 1
                if len(changed_examples) < MAX_PROVENANCE_DETAILS:
                    fields = "、".join(changed_fields)
                    changed_examples.append(
                        f"第 {row.source_row_no} 列的{fields}與原量測 #{stored.id} 不同。"
                    )
        move = moves.get((row.lot_no, row.station_code))
        if (
            move is not None
            and row.machine_code != move.machine_code
            and row.source_row_no not in move_rows
        ):
            move_rows.add(row.source_row_no)
            if len(move_examples) < MAX_PROVENANCE_DETAILS:
                move_examples.append(
                    f"第 {row.source_row_no} 列（{row.lot_no}、{row.station_code}）"
                    f"量測機台為 {row.machine_code}，進出站紀錄為 {move.machine_code}。"
                )

    warnings = [f"重用 {len(existing)} 筆既有量測。"] if existing else []
    if changed_count:
        warnings.append(f"其中 {changed_count} 筆的量測來源資訊有差異，已保留原紀錄。")
        warnings.extend(changed_examples)
    if move_rows:
        warnings.append(
            f"有 {len(move_rows)} 列的量測機台與同批同站的進出站紀錄不同。"
            "進出站紀錄可作為製程參考，請核對量測來源。"
        )
        warnings.extend(move_examples)
    return warnings


def _save_measurements(session: Session, batch_id: int, measurements: list[Measurement]) -> None:
    for group in batched(measurements, 1000):
        session.execute(
            insert(InspectionRecord),
            [
                {
                    "lot_no": row.lot_no,
                    "station_code": row.station_code,
                    "item_code": row.item_code,
                    "panel": row.panel,
                    "point": row.point,
                    "machine_code": row.machine_code,
                    "measured_at": row.measured_at,
                    "operator": row.operator,
                    "value": row.value,
                    "source_row_no": row.source_row_no,
                    "import_batch_id": batch_id,
                }
                for row in group
            ],
        )


def _db_text(value: str | None) -> str | None:
    return value.replace("\x00", "\\u0000") if value is not None else None


class UnapprovedTemplate(ValueError):
    """The requested upload rule does not exist or is still a draft."""


def import_file(
    session: Session,
    file_name: str,
    data: bytes,
    *,
    operators_path: Path = OPERATORS_PATH,
    template_id: int | None = None,
) -> int:
    """Import one upload, preserving attempts and all-or-nothing measurements."""
    digest = sha256(data).hexdigest()
    with session.begin():
        # Serialize writers across all import entry points, including concurrent duplicate uploads.
        session.execute(text("LOCK TABLE import_batch IN SHARE ROW EXCLUSIVE MODE"))
        template = session.get(ImportTemplate, template_id) if template_id is not None else None
        if template_id is not None and (template is None or template.status != "approved"):
            raise UnapprovedTemplate("只能使用已核准的匯入範本")
        original = session.scalar(
            select(ImportBatch)
            .where(
                ImportBatch.file_sha256 == digest,
                ImportBatch.duplicate_of_id.is_(None),
                or_(ImportBatch.status == ACCEPT, ImportBatch.template_id == template_id),
            )
            .order_by((ImportBatch.status == ACCEPT).desc(), ImportBatch.id)
            .limit(1)
        )
        if original is not None:
            attempt = ImportBatch(
                file_name=file_name,
                file_sha256=digest,
                source_type=original.source_type,
                template_id=original.template_id,
                station_code=original.station_code,
                status=DUPLICATE_FILE,
                total_rows=original.total_rows,
                error_rows=0,
                duplicate_of_id=original.id,
            )
            session.add(attempt)
            session.flush()
            return attempt.id

        issues: list[ImportIssue] = []
        measurements: dict[MeasurementKey, Measurement] = {}
        pending: list[Measurement] = []
        total_rows = 0
        source_type = "UNKNOWN"
        station = None
        warnings: list[str] = []
        try:
            if file_name.lower().endswith(".pdf") and template is None:
                sheet = map_lab_pdf(data)
            elif template is None:
                sheet = map_import_file(file_name, data)
            else:
                rules = TemplateRules.model_validate(template, from_attributes=True)
                sheet = map_template_file(file_name, data, rules)
        except (FileReadError, HeaderMappingError) as exc:
            issues.append(ImportIssue(None, None, exc.code, str(exc)))
        else:
            total_rows = len(sheet.rows)
            source_type = sheet.source_type
            warnings = [
                f"第 {column.column_no} 欄 {column.header!r} 未使用"
                for column in sheet.unused_columns
            ]
            if not sheet.rows or len(sheet.points) * total_rows > MAX_MEASUREMENTS:
                issues.append(
                    ImportIssue(None, None, E_FILE_FORMAT, "檔案沒有資料列或量測筆數超過限制")
                )
            else:
                master = _master_data(session, operators_path)
                for row in sheet.rows:
                    result = validate_row(sheet, row, master)
                    if isinstance(result, ImportIssue):
                        issues.append(result)
                    else:
                        keys = {value.key for value in result}
                        if len(keys) != len(result) or not keys.isdisjoint(measurements):
                            issues.append(
                                ImportIssue(
                                    row.line_no, None, E_DUPLICATE_IN_FILE, "同一份檔案含有重複量測"
                                )
                            )
                        else:
                            measurements.update((value.key, value) for value in result)
                stations = {row.station_code for row in measurements.values()}
                station = next(iter(stations)) if len(stations) == 1 else None
                existing = _existing_records(session, measurements)
                conflict_rows = {issue.row_no for issue in issues}
                for key, row in measurements.items():
                    stored = existing.get(key)
                    if stored is None:
                        pending.append(row)
                    elif row.value != stored.value and row.source_row_no not in conflict_rows:
                        issues.append(
                            ImportIssue(
                                row.source_row_no,
                                row.value_column,
                                E_MEASUREMENT_CONFLICT,
                                "同一筆量測已有不同數值："
                                f"原值 {stored.value:g}，新值 {row.value:g}",
                                row.raw_value,
                            )
                        )
                        conflict_rows.add(row.source_row_no)
                if not issues:
                    warnings.extend(_provenance_warnings(measurements, existing, master.moves))

        attempt = ImportBatch(
            file_name=file_name,
            file_sha256=digest,
            source_type=source_type,
            template_id=template_id,
            station_code=station,
            status=REJECT if issues else ACCEPT,
            total_rows=total_rows,
            error_rows=sum(issue.row_no is not None for issue in issues),
            warnings=warnings,
        )
        session.add(attempt)
        session.flush()
        if issues:
            session.add_all(
                [
                    ImportErrorDetail(
                        import_batch_id=attempt.id,
                        row_no=issue.row_no,
                        column=_db_text(issue.column),
                        code=issue.code,
                        message=_db_text(issue.message),
                        raw_value=_db_text(issue.raw_value),
                    )
                    for issue in issues
                ]
            )
        else:
            _save_measurements(session, attempt.id, pending)
        return attempt.id
