"""Transactional, additive-only export orchestrator (mh3.2).

Wraps the mh3.1 INSERT primitive (:func:`app.db.writer.write_project`) in the full
safety path:

    resolve target -> ensure_backup -> BEGIN IMMEDIATE -> write -> record provenance
    -> validate -> additive/FK/integrity assertions -> COMMIT (or ROLLBACK) -> bookkeeping

Guarantees: only INSERTs; new rowid-alias Ids never collide; the whole thing is one
transaction (provenance table + rows commit atomically with the data); on any failure
the DB is left byte-unchanged. Writes operate in place on the configured Target
Scheduler database, with a consistent backup taken before every write.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from ..config import find_source_db
from .backup import backup_signature, ensure_backup
from .ops import OpLogEntry, find_op, mark_status, record_op, set_target_state
from .provenance import (
    ProvRow,
    delete_operation_rows,
    ensure_provenance_table,
    has_progress,
    record_rows,
    rows_for_operation,
)
from .validate import default_validate, validate_exposure_template
from .writer import (
    ExposureTemplateSpec,
    ProjectSpec,
    WriteResult,
    update_project as writer_update_project,
    write_exposure_template,
    write_project,
)

# Tables the writer touches, in FK-insert order (project before its ruleweight children).
WRITTEN_TABLES = ("project", "exposuretemplate", "target", "exposureplan", "ruleweight")

# Tables with no `guid` column — provenance records guid=None and undo deletes by Id.
GUIDLESS_TABLES = frozenset({"ruleweight"})

# How long to wait for the write lock before treating the DB as busy. A live DB
# NINA holds open will fail fast rather than hang the request.
CONNECT_TIMEOUT = 5.0


class ExportError(Exception):
    pass


class DatabaseBusyError(ExportError):
    """The target DB is locked (e.g. NINA/Target Scheduler has it open)."""


class ProgressError(ExportError):
    """Refused because the affected project has captured subframes."""


class EditNotAllowedError(ExportError):
    """Refused to edit a project that isn't a safely-editable Draft (o2c)."""


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


class TemplateResult(BaseModel):
    operation_id: str
    target_db: str
    backup_path: str
    template_id: int
    template_guid: str | None
    counts: dict[str, int]
    committed: bool = True


# --- helpers ---------------------------------------------------------------

def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in WRITTEN_TABLES}


def _max_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COALESCE(MAX(Id), 0) FROM {t}").fetchone()[0]
        for t in WRITTEN_TABLES
    }


def _guid(conn: sqlite3.Connection, table: str, row_id: int) -> str | None:
    if table in GUIDLESS_TABLES:  # no guid column to read
        return None
    r = conn.execute(f"SELECT guid FROM {table} WHERE Id = ?", (row_id,)).fetchone()
    return r[0] if r else None


def _inserted_ids(result: WriteResult) -> dict[str, list[int]]:
    return {
        "project": [result.project_id],
        "exposuretemplate": list(result.template_ids.values()),
        "target": list(result.target_ids),
        "exposureplan": list(result.plan_ids),
        "ruleweight": list(result.ruleweight_ids),
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


def _resolve_target(target_db: str | Path | None) -> Path:
    """The database to read/write in place: an explicit arg, else the configured DB."""
    if target_db is not None:
        return Path(target_db)
    source = find_source_db()
    if source is None:
        raise ExportError(
            "no Target Scheduler database found — set TS_ASSISTANT_DB or drop one "
            "into sample_database/."
        )
    return source


def _busy_or_export_error(e: sqlite3.OperationalError) -> ExportError:
    msg = str(e).lower()
    if "lock" in msg or "busy" in msg:
        return DatabaseBusyError(
            "Target Scheduler database is busy — pause NINA's scheduler or close it, then retry."
        )
    return ExportError(str(e))


# --- public API ------------------------------------------------------------

def export_project(
    spec: ProjectSpec,
    *,
    target_db: str | Path | None = None,
    validate: Callable[[sqlite3.Connection, ProjectSpec], None] | None = None,
    now: datetime | None = None,
) -> ExportResult:
    now = now or datetime.now(timezone.utc)
    validate = validate or default_validate
    target = _resolve_target(target_db)

    backup = ensure_backup(target, now=now)  # consistent restore point before the write
    operation_id = "op_" + uuid.uuid4().hex

    conn = sqlite3.connect(target, isolation_level=None, timeout=CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row  # introspect()/validate look up columns by name
    try:
        conn.execute("PRAGMA foreign_keys = ON")  # no-op inside a txn, so set first
        # Verify integrity BEFORE taking the write lock, so a scan never blocks NINA
        # mid-capture.
        chk = conn.execute("PRAGMA quick_check").fetchone()[0]
        if chk != "ok":
            raise ExportError(f"quick_check failed before write: {chk}")
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


def create_exposure_template(
    spec: ExposureTemplateSpec,
    *,
    target_db: str | Path | None = None,
    validate: Callable[[sqlite3.Connection, ExposureTemplateSpec], None] | None = None,
    now: datetime | None = None,
) -> TemplateResult:
    """Additively create one exposure template through the full safety path.

    Same backup + BEGIN IMMEDIATE + provenance + additive-assert wrapper as
    :func:`export_project`, but for a single INSERT. Provenanced, so the existing
    :func:`undo_operation` removes it.
    """
    now = now or datetime.now(timezone.utc)
    validate = validate or validate_exposure_template
    target = _resolve_target(target_db)

    backup = ensure_backup(target, now=now)  # consistent restore point before the write
    operation_id = "op_" + uuid.uuid4().hex

    conn = sqlite3.connect(target, isolation_level=None, timeout=CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        chk = conn.execute("PRAGMA quick_check").fetchone()[0]  # verify before locking
        if chk != "ok":
            raise ExportError(f"quick_check failed before write: {chk}")
        count_before = conn.execute("SELECT COUNT(*) FROM exposuretemplate").fetchone()[0]
        max_before = conn.execute("SELECT COALESCE(MAX(Id), 0) FROM exposuretemplate").fetchone()[0]

        conn.execute("BEGIN IMMEDIATE")
        ensure_provenance_table(conn)
        validate(conn, spec)
        template_id = write_exposure_template(conn, spec)
        guid = _guid(conn, "exposuretemplate", template_id)
        record_rows(
            conn,
            operation_id,
            [ProvRow(table="exposuretemplate", id=template_id, guid=guid)],
            now.isoformat(),
        )
        count_after = conn.execute("SELECT COUNT(*) FROM exposuretemplate").fetchone()[0]
        if count_after - count_before != 1:
            raise ExportError(
                f"additive check failed for exposuretemplate: +{count_after - count_before}, expected +1"
            )
        if template_id <= max_before:
            raise ExportError(
                f"non-additive id reuse in exposuretemplate: Id {template_id} <= prior max {max_before}"
            )
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ExportError(f"foreign key violations: {fk[:5]}")
        conn.commit()
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
    counts = {"exposuretemplate": 1}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="create_template",
            target_db=target_key,
            backup_path=backup.path,
            status="committed",
            project_guid=None,
            counts=counts,
        )
    )
    return TemplateResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=backup.path,
        template_id=template_id,
        template_guid=guid,
        counts=counts,
    )


def _delete_provenanced_rows(conn: sqlite3.Connection, rows: list[ProvRow]) -> dict[str, int]:
    by_table: dict[str, list[ProvRow]] = {}
    for r in rows:
        by_table.setdefault(r.table, []).append(r)

    deleted: dict[str, int] = {}
    # ruleweight (a guidless child of project) must go before project for FK integrity.
    for table in ("exposureplan", "ruleweight", "target", "exposuretemplate", "project"):
        for r in by_table.get(table, []):
            # A template shared by other (non-deleted) plans must survive.
            if table == "exposuretemplate":
                refs = conn.execute(
                    "SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = ?", (r.id,)
                ).fetchone()[0]
                if refs:
                    continue
            if table in GUIDLESS_TABLES:  # no guid to match on — delete by Id
                cur = conn.execute(f"DELETE FROM {table} WHERE Id = ?", (r.id,))
            elif r.guid is None:
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


def _assert_editable(conn: sqlite3.Connection, project_id: int) -> None:
    """Refuse unless the project is a safely-editable Draft (o2c). Must run inside
    the write txn, before any UPDATE."""
    row = conn.execute("SELECT state FROM project WHERE Id = ?", (project_id,)).fetchone()
    if row is None:
        raise EditNotAllowedError(f"project {project_id} not found")
    if int(row["state"]) != 0:
        raise EditNotAllowedError("only Draft projects can be edited")
    if has_progress(conn, project_id):
        raise ProgressError(
            f"project {project_id} has captured subframes; refusing to edit started work"
        )
    # filtercadence / override-order reference a target's plans by positional index, so
    # editing plans would corrupt them — refuse rather than silently break them.
    tids = [r[0] for r in conn.execute("SELECT Id FROM target WHERE projectid = ?", (project_id,))]
    if tids:
        placeholders = ",".join("?" * len(tids))
        for table in ("filtercadenceitem", "overrideexposureorderitem"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=?", (table,)
            ).fetchone()
            if not exists:
                continue
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE targetid IN ({placeholders})", tids
            ).fetchone()[0]
            if n:
                raise EditNotAllowedError(
                    "this project uses a custom filter cadence or exposure order, which "
                    "TS Assistant can't edit yet"
                )


def update_project(
    project_id: int,
    spec: ProjectSpec,
    *,
    target_db: str | Path | None = None,
    validate: Callable[[sqlite3.Connection, ProjectSpec], None] | None = None,
    now: datetime | None = None,
) -> ExportResult:
    """Edit an existing Draft project in place (o2c) through the full safety path.

    Same backup + BEGIN IMMEDIATE + integrity + FK-check wrapper as
    :func:`export_project`, but UPDATEs an existing project (keeping Ids stable)
    instead of inserting a new one. NOT provenanced / not op-undoable — the
    automatic pre-write backup is the recovery path. Refuses unless the project is a
    Draft with no captured progress and no custom cadence/override (``_assert_editable``).
    """
    now = now or datetime.now(timezone.utc)
    validate = validate or default_validate
    target = _resolve_target(target_db)

    backup = ensure_backup(target, now=now)  # consistent restore point before the write
    operation_id = "op_" + uuid.uuid4().hex

    conn = sqlite3.connect(target, isolation_level=None, timeout=CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        chk = conn.execute("PRAGMA quick_check").fetchone()[0]  # verify before locking
        if chk != "ok":
            raise ExportError(f"quick_check failed before write: {chk}")

        conn.execute("BEGIN IMMEDIATE")
        _assert_editable(conn, project_id)
        validate(conn, spec)  # schema/profile compatibility (reused create-path gate)
        result = writer_update_project(conn, project_id, spec)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ExportError(f"foreign key violations: {fk[:5]}")
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
    counts = {"target": len(result.target_ids), "exposureplan": len(result.plan_ids)}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="update",
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

    # Undo is non-additive (it deletes rows), so it gets the same consistent pre-write
    # backup as any other write.
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
