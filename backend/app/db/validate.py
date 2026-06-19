"""Pre-write validation of the target Target Scheduler database (bead mh3.3).

Run by the export orchestrator INSIDE the transaction, *before* the inserts, so a
schema-incompatible target produces a clear :class:`ValidationError` (→ rollback,
no partial write) instead of a cryptic mid-INSERT SQLite error. Injected via the
``validate=`` parameter of :func:`app.db.export.export_project`.

What it checks (the "schema / FK / EF integrity" the bead asks for):

* **Schema present** — all canonical Target Scheduler tables exist.
* **Migration compatibility** — every column the writer populates exists in the
  target, and the target has no NOT-NULL-without-default column that the writer
  doesn't fill. This tolerates additive schema drift (newer TS versions add
  columns *with* defaults via ``ALTER TABLE``) but rejects incompatible drift.
* **Profile id present** — it scopes every row Target Scheduler reads.

Note on "EF concurrency/migration constraints": the Target Scheduler DB is *not*
EF Core (no ``__EFMigrationsHistory``, no rowversion/concurrency token — verified
in mh3.1), so the practical equivalent is the column-compatibility check above.
Post-write FK/additive/integrity guarantees are owned by the orchestrator
(:mod:`app.db.export`), which runs ``PRAGMA foreign_key_check`` itself.
"""

from __future__ import annotations

import sqlite3

from .introspect import introspect
from .schema import EXPECTED_TABLES
from .writer import WRITTEN_COLUMNS, ProjectSpec


class ValidationError(Exception):
    pass


def validate_schema(conn: sqlite3.Connection, spec: ProjectSpec) -> None:
    """Verify the target DB can accept our additive write. Raises on any problem."""
    if not (spec.profile_id and spec.profile_id.strip()):
        raise ValidationError("profile_id is required")

    conn.row_factory = sqlite3.Row  # introspect looks columns up by name
    tables = {name.lower(): t for name, t in introspect(conn).items()}

    missing_tables = {t.lower() for t in EXPECTED_TABLES} - set(tables)
    if missing_tables:
        raise ValidationError(
            f"target is not a recognized Target Scheduler DB; missing tables: "
            f"{sorted(missing_tables)}"
        )

    for table, written in WRITTEN_COLUMNS.items():
        t = tables[table.lower()]
        have = {c.name.lower() for c in t.columns}
        written_lower = {c.lower() for c in written}

        absent = {c for c in written if c.lower() not in have}
        if absent:
            raise ValidationError(
                f"incompatible Target Scheduler schema: table '{table}' is missing "
                f"column(s) we write: {sorted(absent)}"
            )

        # A NOT NULL column with no default that we don't populate would make our
        # INSERT fail — that's a schema version we can't safely write to.
        for c in t.columns:
            if c.pk:  # rowid alias — auto-assigned
                continue
            if c.notnull and c.default is None and c.name.lower() not in written_lower:
                raise ValidationError(
                    f"incompatible Target Scheduler schema: '{table}.{c.name}' is "
                    f"NOT NULL without a default and is not written by TS Assistant"
                )


# The injectable default hook (pre-write). Kept as the seam name from mh3.2.
default_validate = validate_schema
