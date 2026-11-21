"""Trace parent and child lots using the stored production relationships."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

type TraceDirection = Literal["up", "down"]


@dataclass(frozen=True)
class TraceLink:
    parent_lot_no: str
    child_lot_no: str
    relation: str
    depth: int


def trace_lot(
    session: Session, lot_no: str, direction: TraceDirection, *, max_depth: int = 10
) -> list[TraceLink]:
    """Return reachable links in one direction using a recursive PostgreSQL query.

    Keep parent/child orientation as stored regardless of search direction.
    The first hop has depth 1. Do not revisit a lot within the current path.
    If multiple paths reach the same link, report it once at its shortest depth.
    A lot with no links in that direction returns an empty list.
    Setting max_depth to zero returns no hops.
    """
    if direction == "up":
        start_column, next_column = "child_lot_no", "parent_lot_no"
    elif direction == "down":
        start_column, next_column = "parent_lot_no", "child_lot_no"
    else:
        raise ValueError("Trace direction must be up or down.")

    if max_depth < 0:
        raise ValueError("Trace depth must be nonnegative.")

    # Only hard-coded column names vary. The lot number and depth are parameters.
    query = text(
        f"""
        WITH RECURSIVE reached AS (
            SELECT edge.parent_lot_no, edge.child_lot_no, edge.relation,
                   1 AS depth,
                   CAST(ARRAY[edge.{start_column}, edge.{next_column}] AS text[]) AS path
            FROM lot_link AS edge
            WHERE edge.{start_column} = :lot_no AND :max_depth >= 1

            UNION ALL

            SELECT edge.parent_lot_no, edge.child_lot_no, edge.relation,
                   previous.depth + 1,
                   array_append(previous.path, CAST(edge.{next_column} AS text))
            FROM reached AS previous
            JOIN lot_link AS edge ON edge.{start_column} = previous.{next_column}
            WHERE previous.depth < :max_depth
              AND NOT (edge.{next_column} = ANY(previous.path))
        )
        SELECT parent_lot_no, child_lot_no, relation, MIN(depth) AS depth
        FROM reached
        GROUP BY parent_lot_no, child_lot_no, relation
        ORDER BY depth, parent_lot_no, child_lot_no
        """
    )
    rows = session.execute(query, {"lot_no": lot_no, "max_depth": max_depth})
    return [TraceLink(parent, child, relation, depth) for parent, child, relation, depth in rows]
