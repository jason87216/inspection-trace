"""Database engine, per-request session, and the declarative base for ORM models."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from inspection_trace.config import get_settings

# Give every constraint and index a deterministic name. Without names, PostgreSQL
# picks its own, and Alembic cannot later drop or alter a constraint it cannot name.
# CHECK constraints have no column to derive a name from, so each one must be given
# a short name (e.g. "positive"), which this convention turns into "ck_<table>_positive".
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for ORM models in models.py. Alembic reads Base.metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> Engine:
    """Create the engine once, on first use. Creating it does not open a connection yet."""
    return create_engine(get_settings().database_url)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed when the request ends."""
    with Session(get_engine()) as session:
        yield session
