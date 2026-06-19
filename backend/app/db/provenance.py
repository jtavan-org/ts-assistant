"""In-DB provenance — the authoritative record of which rows TS Assistant added.

A single clearly-namespaced table, ``ts_assistant_provenance``, is created in the
target database (additive: a new table; NINA never enumerates it) and written
*inside the same transaction* as the rows it describes, so provenance is atomic
with the data and travels with the DB. This is what proves the additive contract
and what ``undo`` deletes against.

Also home to the **progress gate**: a project/target that has captured subframes is
off-limits (we never remove or modify started work).
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

PROVENANCE_TABLE = "ts_assistant_provenance"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {PROVENANCE_TABLE} (
    id           INTEGER PRIMARY KEY,
    operation_id TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    row_id       INTEGER NOT NULL,
    guid         TEXT,
    created_utc  TEXT NOT NULL
)
"""


class ProvRow(BaseModel):
    table: str
    id: int
    guid: str | None = None


def ensure_provenance_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)


def record_rows(
    conn: sqlite3.Connection,
    operation_id: str,
    rows: list[ProvRow],
    created_utc: str,
) -> None:
    conn.executemany(
        f"INSERT INTO {PROVENANCE_TABLE}"
        " (operation_id, table_name, row_id, guid, created_utc) VALUES (?, ?, ?, ?, ?)",
        [(operation_id, r.table, r.id, r.guid, created_utc) for r in rows],
    )


def rows_for_operation(conn: sqlite3.Connection, operation_id: str) -> list[ProvRow]:
    cur = conn.execute(
        f"SELECT table_name, row_id, guid FROM {PROVENANCE_TABLE} WHERE operation_id = ?",
        (operation_id,),
    )
    return [ProvRow(table=t, id=i, guid=g) for (t, i, g) in cur.fetchall()]


def delete_operation_rows(conn: sqlite3.Connection, operation_id: str) -> None:
    conn.execute(
        f"DELETE FROM {PROVENANCE_TABLE} WHERE operation_id = ?", (operation_id,)
    )


def has_progress(conn: sqlite3.Connection, project_id: int) -> bool:
    """True if a project (or any of its targets) has captured data — off-limits.

    Progress = any acquiredimage row for the project, OR any exposureplan.acquired > 0
    for one of its targets. Identifier case is irrelevant to SQLite.
    """
    images = conn.execute(
        "SELECT COUNT(*) FROM acquiredimage WHERE projectId = ?", (project_id,)
    ).fetchone()[0]
    if images:
        return True
    acquired = conn.execute(
        "SELECT COALESCE(SUM(acquired), 0) FROM exposureplan ep"
        " JOIN target t ON t.Id = ep.targetid WHERE t.projectid = ?",
        (project_id,),
    ).fetchone()[0]
    return bool(acquired)
