"""Transactional, additive-only export orchestrator (mh3.2).

Wraps the mh3.1 INSERT primitive (:func:`app.db.writer.write_project`) in the full
safety path:

    resolve target -> ensure_backup -> BEGIN IMMEDIATE -> write -> record provenance
    -> validate -> additive/FK/integrity assertions -> COMMIT (or ROLLBACK) -> bookkeeping

Guarantees: only INSERTs; new rowid-alias Ids never collide; the whole thing is one
transaction (provenance table + rows commit atomically with the data); on any failure
the DB is left byte-unchanged. Writing to the *live* source DB is gated behind
``TS_ASSISTANT_ALLOW_LIVE_WRITE``; the default target is a safe staging copy.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from ..config import (
    EXPORT_DB,
    allow_live_write,
    ensure_dirs,
    find_source_db,
    integrity_check_enabled,
)
from .backup import backup_signature, consistent_copy, ensure_backup
from .ops import OpLogEntry, find_op, mark_status, record_op, set_target_state
from .provenance import (
    ProvRow,
    delete_operation_rows,
    ensure_provenance_table,
    has_progress,
    record_rows,
    rows_for_operation,
)
from .validate import default_validate
from .writer import ProjectSpec, WriteResult, create_scheduler_db, write_project

# Tables the writer touches, in FK-insert order.
WRITTEN_TABLES = ("project", "exposuretemplate", "target", "exposureplan")

# How long to wait for the write lock before treating the DB as busy. A live DB
# NINA holds open will fail fast rather than hang the request.
CONNECT_TIMEOUT = 5.0


class ExportError(Exception):
    pass


class DatabaseBusyError(ExportError):
    """The target DB is locked (e.g. NINA/Target Scheduler has it open)."""


class ProgressError(ExportError):
    """Refused because the affected project has captured subframes."""


class ExportResult(BaseModel):
    operation_id: str
    target_db: str
    backup_path: str
    project_id: int
    project_guid: str | None
    target_ids: list[int]
    plan_ids: list[int]
    template_ids: dict[str, int]
    counts: dict[str, int]  # rows we added, per table
    committed: bool = True


class UndoResult(BaseModel):
    operation_id: str
    target_db: str
    backup_path: str
    deleted: dict[str, int]


# --- helpers ---------------------------------------------------------------

def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in WRITTEN_TABLES}


def _max_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COALESCE(MAX(Id), 0) FROM {t}").fetchone()[0]
        for t in WRITTEN_TABLES
    }


def _guid(conn: sqlite3.Connection, table: str, row_id: int) -> str | None:
    r = conn.execute(f"SELECT guid FROM {table} WHERE Id = ?", (row_id,)).fetchone()
    return r[0] if r else None


def _inserted_ids(result: WriteResult) -> dict[str, list[int]]:
    return {
        "project": [result.project_id],
        "exposuretemplate": list(result.template_ids.values()),
        "target": list(result.target_ids),
        "exposureplan": list(result.plan_ids),
    }


def _collect_prov(conn: sqlite3.Connection, result: WriteResult) -> list[ProvRow]:
    rows: list[ProvRow] = []
    for table, ids in _inserted_ids(result).items():
        for row_id in ids:
            rows.append(ProvRow(table=table, id=row_id, guid=_guid(conn, table, row_id)))
    return rows


def _assert_additive(
    conn: sqlite3.Connection,
    counts_before: dict[str, int],
    max_before: dict[str, int],
    result: WriteResult,
) -> None:
    inserted = _inserted_ids(result)
    counts_now = _counts(conn)
    for t in WRITTEN_TABLES:
        delta = counts_now[t] - counts_before[t]
        if delta != len(inserted[t]):
            raise ExportError(f"additive check failed for {t}: +{delta}, expected +{len(inserted[t])}")
        for row_id in inserted[t]:
            if row_id <= max_before[t]:
                raise ExportError(f"non-additive id reuse in {t}: Id {row_id} <= prior max {max_before[t]}")


def _seed_export_db() -> Path:
    ensure_dirs()
    if not EXPORT_DB.exists():
        source = find_source_db()
        if source is not None and source.is_file():
            consistent_copy(source, EXPORT_DB)  # NINA-importable clone, not the live file
        else:
            create_scheduler_db(EXPORT_DB).close()  # empty canonical DB
    return EXPORT_DB


def _resolve_target(target_db: str | Path | None) -> Path:
    if target_db is None:
        return _seed_export_db()
    target = Path(target_db)
    source = find_source_db()
    if source is not None and target.resolve() == source.resolve() and not allow_live_write():
        raise ExportError(
            "refusing to write to the live Target Scheduler DB; "
            "set TS_ASSISTANT_ALLOW_LIVE_WRITE=1 to allow live writes"
        )
    return target


def _busy_or_export_error(e: sqlite3.OperationalError) -> ExportError:
    msg = str(e).lower()
    if "lock" in msg or "busy" in msg:
        return DatabaseBusyError("target database is busy (is NINA / Target Scheduler open?)")
    return ExportError(str(e))


# --- public API ------------------------------------------------------------

def export_project(
    spec: ProjectSpec,
    *,
    target_db: str | Path | None = None,
    backup_window_min: int | None = None,
    validate: Callable[[sqlite3.Connection, ProjectSpec], None] | None = None,
    now: datetime | None = None,
) -> ExportResult:
    now = now or datetime.now(timezone.utc)
    validate = validate or default_validate
    target = _resolve_target(target_db)

    backup = ensure_backup(target, window_min=backup_window_min, now=now)
    operation_id = "op_" + uuid.uuid4().hex

    conn = sqlite3.connect(target, isolation_level=None, timeout=CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row  # introspect()/validate look up columns by name
    try:
        conn.execute("PRAGMA foreign_keys = ON")  # no-op inside a txn, so set first
        counts_before = _counts(conn)
        max_before = _max_ids(conn)

        conn.execute("BEGIN IMMEDIATE")  # take the write lock up front; busy -> fail fast
        ensure_provenance_table(conn)  # inside txn so a rollback removes it too
        validate(conn, spec)  # pre-write schema/compat gate (mh3.3); clean error before INSERTs
        result = write_project(conn, spec)
        record_rows(conn, operation_id, _collect_prov(conn, result), now.isoformat())
        _assert_additive(conn, counts_before, max_before, result)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ExportError(f"foreign key violations: {fk[:5]}")
        if integrity_check_enabled():
            status = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if status != "ok":
                raise ExportError(f"integrity_check failed: {status}")
        conn.commit()
        project_guid = _guid(conn, "project", result.project_id)
    except sqlite3.OperationalError as e:
        conn.rollback()
        raise _busy_or_export_error(e) from e
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    target_key = str(target.resolve())
    set_target_state(target_key, last_seen_signature=backup_signature(target))
    counts = {t: len(ids) for t, ids in _inserted_ids(result).items()}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="export",
            target_db=target_key,
            backup_path=backup.path,
            status="committed",
            project_guid=project_guid,
            counts=counts,
        )
    )
    return ExportResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=backup.path,
        project_id=result.project_id,
        project_guid=project_guid,
        target_ids=result.target_ids,
        plan_ids=result.plan_ids,
        template_ids=result.template_ids,
        counts=counts,
    )


def _delete_provenanced_rows(conn: sqlite3.Connection, rows: list[ProvRow]) -> dict[str, int]:
    by_table: dict[str, list[ProvRow]] = {}
    for r in rows:
        by_table.setdefault(r.table, []).append(r)

    deleted: dict[str, int] = {}
    for table in ("exposureplan", "target", "exposuretemplate", "project"):
        for r in by_table.get(table, []):
            # A template shared by other (non-deleted) plans must survive.
            if table == "exposuretemplate":
                refs = conn.execute(
                    "SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = ?", (r.id,)
                ).fetchone()[0]
                if refs:
                    continue
            if r.guid is None:
                cur = conn.execute(f"DELETE FROM {table} WHERE Id = ? AND guid IS NULL", (r.id,))
            else:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE Id = ? AND guid = ?", (r.id, r.guid)
                )
            if cur.rowcount == 0:
                still = conn.execute(f"SELECT 1 FROM {table} WHERE Id = ?", (r.id,)).fetchone()
                if still is not None:
                    raise ExportError(
                        f"guid mismatch on {table}.{r.id}; row is no longer ours — aborting undo"
                    )
                continue  # already gone
            deleted[table] = deleted.get(table, 0) + cur.rowcount
    return deleted


def undo_operation(
    operation_id: str,
    *,
    target_db: str | Path | None = None,
    now: datetime | None = None,
) -> UndoResult:
    now = now or datetime.now(timezone.utc)
    op = find_op(operation_id)
    if op is None and target_db is None:
        raise ExportError(f"unknown operation {operation_id}")
    target = Path(target_db) if target_db else Path(op.target_db)  # type: ignore[union-attr]

    source = find_source_db()
    if source is not None and target.resolve() == source.resolve() and not allow_live_write():
        raise ExportError("refusing to undo on the live DB; set TS_ASSISTANT_ALLOW_LIVE_WRITE=1")

    backup = ensure_backup(target, now=now)
    conn = sqlite3.connect(target, isolation_level=None, timeout=CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row  # introspect()/validate look up columns by name
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        rows = rows_for_operation(conn, operation_id)
        if not rows:
            raise ExportError(f"no provenance rows for {operation_id} in {target}")
        for r in rows:
            if r.table == "project" and has_progress(conn, r.id):
                raise ProgressError(
                    f"project {r.id} has captured subframes; refusing to undo started work"
                )
        deleted = _delete_provenanced_rows(conn, rows)
        delete_operation_rows(conn, operation_id)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ExportError(f"foreign key violations after undo: {fk[:5]}")
        conn.commit()
    except sqlite3.OperationalError as e:
        conn.rollback()
        raise _busy_or_export_error(e) from e
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    set_target_state(str(target.resolve()), last_seen_signature=backup_signature(target))
    mark_status(operation_id, "undone")
    return UndoResult(
        operation_id=operation_id,
        target_db=str(target.resolve()),
        backup_path=backup.path,
        deleted=deleted,
    )
