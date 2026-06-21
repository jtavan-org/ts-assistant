"""Transactional, additive-only export orchestrator (mh3.2).

Wraps the mh3.1 INSERT primitive (:func:`app.db.writer.write_project`) in the full
safety path:

    resolve target -> ensure_backup -> BEGIN IMMEDIATE -> write -> record provenance
    -> validate -> additive/FK/integrity assertions -> COMMIT (or ROLLBACK) -> bookkeeping

Guarantees: only INSERTs; new rowid-alias Ids never collide; the whole thing is one
transaction (provenance table + rows commit atomically with the data); on any failure
the DB is left byte-unchanged.

Writes are local-staged: each write copies the configured database to a local working
file, mutates that, then publishes the whole file back to the source with an optimistic
compare-and-swap (see :func:`_staged_write`). This makes writes work even when the
source lives on a network share / file-sync replica (SMB/CIFS/NFS, Syncthing) where
in-place incremental SQLite writes fail; reads still go straight to the source. A
consistent backup is taken before every write.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from pydantic import BaseModel

from ..config import find_source_db
from .backup import backup_signature, consistent_copy, ensure_backup, work_dir
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
    delete_project as writer_delete_project,
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


class DatabaseReadOnlyError(ExportError):
    """We couldn't save: the database's folder isn't writable, so publishing the edited
    copy back to it failed. (Writes themselves run on a local copy — see _staged_write —
    so a network share that merely rejects in-place SQLite writes is fine; this is about
    the destination folder genuinely not accepting a file write.) Distinct from *busy* —
    retrying alone won't help until the folder's permissions are fixed."""


class ProgressError(ExportError):
    """Refused because the affected project has captured subframes."""


class EditNotAllowedError(ExportError):
    """Refused to edit a project that isn't a safely-editable Draft (o2c)."""


class ConcurrentModificationError(DatabaseBusyError):
    """The database changed on disk between when we staged a local copy and when we
    went to publish our result — another writer (e.g. NINA, or a file-sync pulling a
    newer version) wrote it underneath us. We refuse to overwrite their change; the
    user just retries. Subclasses DatabaseBusyError so it maps to 409 like other
    "can't write right now" conditions."""


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


class DeleteResult(BaseModel):
    project_id: int
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
    if "readonly" in msg or "read-only" in msg or "read only" in msg:
        # Writes run on a local working copy, so this is unusual (the local scratch dir
        # is read-only?). Report it plainly rather than blaming the source location.
        return DatabaseReadOnlyError(
            "Can't write the working copy of the database — the local working directory "
            f"appears to be read-only. ({e})"
        )
    return ExportError(str(e))


# --- local-staged writes ---------------------------------------------------
#
# All writes are performed on a LOCAL working copy of the database, which is then
# published back to the source as a whole file. This is what makes writes work when
# the configured database lives on a network share / file-sync replica (SMB/CIFS/NFS,
# Syncthing, …): incremental in-place SQLite writes (locking, a -journal, random page
# writes) are unreliable there and fail with "attempt to write a readonly database",
# but reads and whole-file copies are fine. We guard against clobbering a concurrent
# external writer with an optimistic compare-and-swap on the source's signature — no
# lock needed, which suits the infrequent, single-user write pattern.


class _StagedWrite:
    """Handle yielded by :func:`_staged_write`: open ``work``, key bookkeeping off
    ``source``, and read ``new_signature`` after the block (set on publish)."""

    def __init__(self, source: Path, work: Path, backup):
        self.source = source
        self.work = work
        self.backup = backup
        self.new_signature: str | None = None


def _assert_source_readable(source: Path) -> None:
    """Fail fast (bounded) if a writer holds an exclusive lock on the source.

    The Online Backup API used by :func:`_stage_in` retries on SQLITE_BUSY *forever*, so
    without this probe an exclusive lock would hang the request. A SHARED read here waits
    only up to ``CONNECT_TIMEOUT`` (the connection's busy timeout) and then raises, which
    the caller maps to a clean busy error. In the normal case (source not locked) this is
    a couple of cheap reads."""
    probe = sqlite3.connect(str(source), timeout=CONNECT_TIMEOUT)
    try:
        probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        probe.close()


def _stage_in(source: Path) -> Path:
    """Make a local, self-contained working copy of ``source`` for writing.

    Uses the Online Backup API (consistent even if the source is being read), then
    forces rollback-journal mode so the single ``work`` file is always self-contained
    after a commit (no -wal to carry), which keeps publishing a one-file copy correct.
    """
    wd = work_dir()
    wd.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=source.stem + "-work-", suffix=".sqlite", dir=wd)
    os.close(fd)
    work = Path(tmp)
    try:
        consistent_copy(source, work, timeout=CONNECT_TIMEOUT)
        conn = sqlite3.connect(str(work))
        try:
            conn.execute("PRAGMA journal_mode=DELETE")  # never WAL locally -> file is standalone
        finally:
            conn.close()
    except BaseException:
        work.unlink(missing_ok=True)  # don't leak the temp copy if staging fails
        raise
    return work


def _publish(source: Path, work: Path, expected_sig: str) -> str:
    """Publish the modified ``work`` copy back to ``source`` as a whole file, but only
    if ``source`` is byte-for-byte the snapshot we staged (compare-and-swap). Returns
    the new source signature; raises :class:`ConcurrentModificationError` on a race."""
    if backup_signature(source) != expected_sig:
        raise ConcurrentModificationError(
            "the database changed on disk while the edit was in progress (NINA or a file "
            "sync wrote it). Your change was not applied — reload and try again."
        )
    # Whole-file publish: copy to a temp file in the source directory, then atomically
    # rename over the original (a sequential write + rename, which works over CIFS).
    # Creating the temp file or renaming can fail if the folder isn't writable -> map the
    # whole thing to a clean read-only error.
    try:
        fd, tmp = tempfile.mkstemp(prefix=source.name + ".", suffix=".tswrite", dir=source.parent)
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            shutil.copyfile(work, tmp_path)
            os.replace(tmp_path, source)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    except OSError as e:
        raise DatabaseReadOnlyError(
            f"couldn't save changes back to {source} — the folder containing the database "
            "isn't writable by TS Assistant. Check the permissions on the mounted database "
            "directory."
        ) from e
    return backup_signature(source)


@contextmanager
def _staged_write(
    target_db: str | Path | None, now: datetime
) -> Iterator[_StagedWrite]:
    """Resolve + back up the source, stage a local working copy, and (on a clean exit
    of the block) compare-and-swap publish it back. The caller opens ``ctx.work`` for
    the actual SQLite mutation and keys bookkeeping off ``ctx.source`` afterward."""
    source = _resolve_target(target_db)
    # Snapshot + stage the source locally. Reading it can hit a lock (e.g. a writer
    # mid-commit); surface that as a clean busy error rather than a raw 500.
    try:
        _assert_source_readable(source)  # bounded; avoids the backup API hanging on a lock
        work = _stage_in(source)
        expected_sig = backup_signature(source)  # the state our working copy is based on
    except sqlite3.OperationalError as e:
        raise _busy_or_export_error(e) from e
    try:
        try:
            backup = ensure_backup(source, now=now)  # restore point before the write
        except sqlite3.OperationalError as e:
            raise _busy_or_export_error(e) from e
        ctx = _StagedWrite(source=source, work=work, backup=backup)
        yield ctx
        # Only reached if the block completed without raising (mutation committed).
        ctx.new_signature = _publish(source, work, expected_sig)
    finally:
        work.unlink(missing_ok=True)


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
    operation_id = "op_" + uuid.uuid4().hex

    with _staged_write(target_db, now) as st:
        conn = sqlite3.connect(str(st.work), isolation_level=None, timeout=CONNECT_TIMEOUT)
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

    target_key = str(st.source.resolve())
    set_target_state(target_key, last_seen_signature=st.new_signature)
    counts = {t: len(ids) for t, ids in _inserted_ids(result).items()}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="export",
            target_db=target_key,
            backup_path=st.backup.path,
            status="committed",
            project_guid=project_guid,
            counts=counts,
        )
    )
    return ExportResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=st.backup.path,
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
    operation_id = "op_" + uuid.uuid4().hex

    with _staged_write(target_db, now) as st:
        conn = sqlite3.connect(str(st.work), isolation_level=None, timeout=CONNECT_TIMEOUT)
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

    target_key = str(st.source.resolve())
    set_target_state(target_key, last_seen_signature=st.new_signature)
    counts = {"exposuretemplate": 1}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="create_template",
            target_db=target_key,
            backup_path=st.backup.path,
            status="committed",
            project_guid=None,
            counts=counts,
        )
    )
    return TemplateResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=st.backup.path,
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
            # A target's override-order rows (awh) aren't provenanced and have no FK —
            # remove them with the target so undo doesn't leave orphans.
            if table == "target":
                for tbl in ("overrideexposureorderitem", "filtercadenceitem"):
                    if conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=?", (tbl,)
                    ).fetchone():
                        conn.execute(f"DELETE FROM {tbl} WHERE targetid = ?", (r.id,))
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
    # Filter cadence + override exposure order used to be refused here; awh now manages
    # them (the editor rebuilds override order, and stale cadence is dropped so NINA
    # regenerates it from filterswitchfrequency), so editing such projects is allowed.


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
    operation_id = "op_" + uuid.uuid4().hex

    with _staged_write(target_db, now) as st:
        conn = sqlite3.connect(str(st.work), isolation_level=None, timeout=CONNECT_TIMEOUT)
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

    target_key = str(st.source.resolve())
    set_target_state(target_key, last_seen_signature=st.new_signature)
    counts = {"target": len(result.target_ids), "exposureplan": len(result.plan_ids)}
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="update",
            target_db=target_key,
            backup_path=st.backup.path,
            status="committed",
            project_guid=project_guid,
            counts=counts,
        )
    )
    return ExportResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=st.backup.path,
        project_id=result.project_id,
        project_guid=project_guid,
        target_ids=result.target_ids,
        plan_ids=result.plan_ids,
        template_ids=result.template_ids,
        counts=counts,
    )


def delete_project(
    project_id: int,
    *,
    target_db: str | Path | None = None,
    now: datetime | None = None,
) -> DeleteResult:
    """Delete an existing Draft project (and its rows) in place through the full safety
    path. Same backup + BEGIN IMMEDIATE + integrity + FK-check wrapper as the writes;
    refuses unless the project is editable (``_assert_editable``: Draft, no captured
    progress, no custom cadence/override). The pre-delete backup is the recovery path.
    """
    now = now or datetime.now(timezone.utc)
    operation_id = "op_" + uuid.uuid4().hex

    with _staged_write(target_db, now) as st:
        conn = sqlite3.connect(str(st.work), isolation_level=None, timeout=CONNECT_TIMEOUT)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            chk = conn.execute("PRAGMA quick_check").fetchone()[0]  # verify before locking
            if chk != "ok":
                raise ExportError(f"quick_check failed before write: {chk}")

            conn.execute("BEGIN IMMEDIATE")
            _assert_editable(conn, project_id)
            deleted = writer_delete_project(conn, project_id)
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

    target_key = str(st.source.resolve())
    set_target_state(target_key, last_seen_signature=st.new_signature)
    record_op(
        OpLogEntry(
            operation_id=operation_id,
            written_at=now.isoformat(),
            kind="delete",
            target_db=target_key,
            backup_path=st.backup.path,
            status="committed",
            project_guid=None,
            counts=deleted,
        )
    )
    return DeleteResult(
        project_id=project_id,
        target_db=target_key,
        backup_path=st.backup.path,
        deleted=deleted,
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
    resolved = target_db if target_db else op.target_db  # type: ignore[union-attr]

    # Undo is non-additive (it deletes rows), so it goes through the same staged write +
    # consistent pre-write backup as any other write.
    with _staged_write(resolved, now) as st:
        conn = sqlite3.connect(str(st.work), isolation_level=None, timeout=CONNECT_TIMEOUT)
        conn.row_factory = sqlite3.Row  # introspect()/validate look up columns by name
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            rows = rows_for_operation(conn, operation_id)
            if not rows:
                raise ExportError(f"no provenance rows for {operation_id} in {st.source}")
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

    target_key = str(st.source.resolve())
    set_target_state(target_key, last_seen_signature=st.new_signature)
    mark_status(operation_id, "undone")
    return UndoResult(
        operation_id=operation_id,
        target_db=target_key,
        backup_path=st.backup.path,
        deleted=deleted,
    )
