"""Unit test (no database): Base.metadata's naming convention gives constraints fixed names."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from inspection_trace.db import Base


def build_demo_ddl() -> str:
    """Compile CREATE TABLE for a throwaway table on a copy of the real convention."""
    # A fresh MetaData with the same convention, so Base.metadata stays untouched.
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    Table("parent", metadata, Column("code", String(20), primary_key=True))
    demo = Table(
        "demo",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("a", Integer, nullable=False),
        Column("b", Integer, nullable=False),
        Column("parent_code", String(20), ForeignKey("parent.code"), nullable=False),
        UniqueConstraint("a", "b"),
        CheckConstraint("a > 0", name="positive"),
    )
    return str(CreateTable(demo).compile(dialect=postgresql.dialect()))


def test_primary_key_name() -> None:
    assert "CONSTRAINT pk_demo PRIMARY KEY (id)" in build_demo_ddl()


def test_multi_column_unique_name_includes_all_columns() -> None:
    assert "CONSTRAINT uq_demo_a_b UNIQUE (a, b)" in build_demo_ddl()


def test_check_constraint_name_is_prefixed() -> None:
    assert "CONSTRAINT ck_demo_positive CHECK (a > 0)" in build_demo_ddl()


def test_foreign_key_name() -> None:
    ddl = build_demo_ddl()
    assert "CONSTRAINT fk_demo_parent_code_parent FOREIGN KEY(parent_code)" in ddl
    assert "REFERENCES parent (code)" in ddl
