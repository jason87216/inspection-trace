"""SQLAlchemy ORM models. Table design: docs/DESIGN.md section 5; decisions: docs/DECISIONS.md.

Master data, lots, inspection imports and process waiting-time rules.
Rules used throughout:
- Tables with a business code use it as the primary key; codes never change (P1-2).
- Mapped[X] means NOT NULL; Mapped[X | None] means NULL is allowed, and each such column says
  what NULL means.
- Every time column is timestamptz: DateTime(timezone=True) (P1-3, P1-6).
- CHECK constraints get a short name; db.py's naming convention turns it into ck_<table>_<name>.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from inspection_trace.db import Base


class Station(Base):
    """Process station, e.g. C40 內層線路. The code is the primary key and never changes (P1-2)."""

    __tablename__ = "station"

    # Mapped[str] (not str | None) -> NOT NULL. primary_key=True -> constraint "pk_station".
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    seq: Mapped[int]  # route order; the type alone is enough for a plain NOT NULL integer
    # server_default puts DEFAULT true into the table itself, so rows inserted with raw SQL
    # get it too. (default=True would only apply to rows inserted through Python.)
    active: Mapped[bool] = mapped_column(server_default=true())


class Machine(Base):
    """Machine, e.g. LDR-01 at B20. Valid for measurements in [valid_from, valid_to) (P1-7)."""

    __tablename__ = "machine"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_period"),
    )

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"), index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NULL = still in use.
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InspectionItem(Base):
    """Inspection item and its spec, e.g. PLT_CU 電鍍銅厚度 13–17 μm. Values in `unit` (P1-11)."""

    __tablename__ = "inspection_item"
    __table_args__ = (
        CheckConstraint("kind IN ('continuous', 'count')", name="kind"),
        CheckConstraint("lsl IS NULL OR lsl <= usl", name="spec_order"),
    )

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"))
    kind: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(10))
    # NULL = no lower limit / no target (defect counts, warpage).
    lsl: Mapped[float | None] = mapped_column(Double)
    target: Mapped[float | None] = mapped_column(Double)
    usl: Mapped[float] = mapped_column(Double)
    physical_min: Mapped[float] = mapped_column(Double)  # below this is E_OUT_OF_PHYSICAL_RANGE
    active: Mapped[bool] = mapped_column(server_default=true())


class Part(Base):
    """Part number, e.g. PN-ABF-1001."""

    __tablename__ = "part"

    part_no: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(server_default=true())


class Lot(Base):
    """Production lot (L261102A001) or material lot (ABF-3619) in one table (P1-1).

    part_no, panel_count, released_at belong to production lots only; kind to material lots only.
    NULL in those columns means "not applicable", and the CHECKs below enforce it by lot_type.
    """

    __tablename__ = "lot"
    __table_args__ = (
        CheckConstraint("lot_type IN ('production', 'material')", name="lot_type"),
        CheckConstraint(
            "lot_type <> 'production' OR (part_no IS NOT NULL AND panel_count IS NOT NULL"
            " AND released_at IS NOT NULL AND kind IS NULL)",
            name="production_fields",
        ),
        CheckConstraint(
            "lot_type <> 'material' OR (kind IS NOT NULL AND part_no IS NULL"
            " AND panel_count IS NULL AND released_at IS NULL)",
            name="material_fields",
        ),
        CheckConstraint("panel_count > 0", name="panel_count_positive"),
    )

    lot_no: Mapped[str] = mapped_column(String(30), primary_key=True)
    lot_type: Mapped[str] = mapped_column(String(20))
    part_no: Mapped[str | None] = mapped_column(String(30), ForeignKey("part.part_no"))
    panel_count: Mapped[int | None]
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str | None] = mapped_column(String(10))  # ABF / CORE


class LotLink(Base):
    """Lot relation: consume (material lot -> production lot) or split (lot -> sub-lot).

    "A consume parent is a material lot" spans two rows, so no CHECK can enforce it:
    the seed and the loader are responsible for it.
    """

    __tablename__ = "lot_link"
    __table_args__ = (
        UniqueConstraint("parent_lot_no", "child_lot_no"),
        CheckConstraint("relation IN ('consume', 'split')", name="relation"),
        CheckConstraint("parent_lot_no <> child_lot_no", name="not_self"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    parent_lot_no: Mapped[str] = mapped_column(String(30), ForeignKey("lot.lot_no"))
    child_lot_no: Mapped[str] = mapped_column(String(30), ForeignKey("lot.lot_no"))
    relation: Mapped[str] = mapped_column(String(10))


class LotMove(Base):
    """A lot's visit to a station (simulated MES track-in / track-out). Q-time is computed from it.

    Unique per (lot, station) for now; rework (same lot at the same station twice) would need a
    different key (P1-9).
    """

    __tablename__ = "lot_move"
    __table_args__ = (
        UniqueConstraint("lot_no", "station_code"),
        CheckConstraint("end_at IS NULL OR end_at >= start_at", name="time_order"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    lot_no: Mapped[str] = mapped_column(String(30), ForeignKey("lot.lot_no"))
    station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"))
    machine_code: Mapped[str] = mapped_column(String(20), ForeignKey("machine.code"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NULL = not yet tracked out.
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator: Mapped[str] = mapped_column(String(20))


class QTimeRule(Base):
    """Maximum hours between leaving one station and entering the next specified station."""

    __tablename__ = "qtime_rule"
    __table_args__ = (
        UniqueConstraint("from_station_code", "to_station_code"),
        CheckConstraint("from_station_code <> to_station_code", name="distinct_stations"),
        CheckConstraint(
            "limit_hours > 0 AND limit_hours < 'Infinity'::float8", name="positive_limit"
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    from_station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"))
    to_station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"))
    limit_hours: Mapped[float] = mapped_column(Double)


class ImportTemplate(Base):
    """An editable draft, or the immutable rules used for a past upload."""

    __tablename__ = "import_template"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved')", name="status"),
        CheckConstraint("header_row BETWEEN 1 AND 30", name="header_row"),
        CheckConstraint("file_type IN ('csv', 'xlsx')", name="file_type"),
        CheckConstraint(
            "(status = 'draft' AND approved_by IS NULL AND approved_at IS NULL)"
            " OR (status = 'approved' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approval",
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    file_type: Mapped[str] = mapped_column(String(4))
    header_row: Mapped[int]
    column_map: Mapped[dict[str, str]] = mapped_column(JSON)
    date_format: Mapped[str] = mapped_column(String(12))
    default_unit: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    approved_by: Mapped[str | None] = mapped_column(String(50))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportBatch(Base):
    """An upload attempt, including later uploads of already processed content."""

    __tablename__ = "import_batch"
    __table_args__ = (
        CheckConstraint("total_rows >= 0", name="total_rows"),
        CheckConstraint("error_rows BETWEEN 0 AND total_rows", name="error_rows"),
        CheckConstraint(
            "(status = 'DUPLICATE_FILE') = (duplicate_of_id IS NOT NULL)", name="duplicate_status"
        ),
        Index(
            "uq_import_batch_file_sha256",
            "file_sha256",
            "template_id",
            unique=True,
            postgresql_where=text("duplicate_of_id IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    file_name: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(30))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("import_template.id"))
    status: Mapped[str] = mapped_column(String(24))
    total_rows: Mapped[int]
    error_rows: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id"))
    station_code: Mapped[str | None] = mapped_column(String(20), ForeignKey("station.code"))
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))


class ImportErrorDetail(Base):
    """An error on a source cell, row or file."""

    __tablename__ = "import_error"
    __table_args__ = (CheckConstraint("row_no IS NULL OR row_no > 0", name="row_no"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    row_no: Mapped[int | None]
    column: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    raw_value: Mapped[str | None] = mapped_column(Text)


class InspectionRecord(Base):
    """One normalized measurement, with a pointer to its original file and physical row."""

    __tablename__ = "inspection_record"
    __table_args__ = (
        UniqueConstraint(
            "lot_no",
            "station_code",
            "item_code",
            "panel",
            "point",
            name="uq_inspection_record_measurement",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("panel > 0", name="panel"),
        CheckConstraint("point IS NULL OR point > 0", name="point"),
        CheckConstraint("source_row_no > 0", name="source_row_no"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    lot_no: Mapped[str] = mapped_column(String(30), ForeignKey("lot.lot_no"))
    station_code: Mapped[str] = mapped_column(String(20), ForeignKey("station.code"))
    machine_code: Mapped[str] = mapped_column(String(20), ForeignKey("machine.code"))
    item_code: Mapped[str] = mapped_column(String(30), ForeignKey("inspection_item.code"))
    panel: Mapped[int]
    point: Mapped[int | None]
    value: Mapped[float] = mapped_column(Double)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operator: Mapped[str] = mapped_column(String(20))
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    source_row_no: Mapped[int]


class AuditLog(Base):
    """Record a local MCP call without copying its result or raw input."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(10))
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict[str, str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCallLog(Base):
    """Evaluation request metadata and outcome, excluding prompts and credentials."""

    __tablename__ = "llm_call_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    feature: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    input_digest: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, object]] = mapped_column(JSON)
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
