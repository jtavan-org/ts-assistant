"""Pre-commit write validation (seam for mh3.3).

``default_validate`` runs INSIDE the export transaction, after the inserts and
before COMMIT, raising :class:`ValidationError` to trigger a rollback. mh3.2 ships
the minimal real checks; mh3.3 deepens them and is injected via the ``validate=``
parameter of :func:`app.db.export.export_project` without touching the orchestrator.
"""

from __future__ import annotations

import sqlite3

from .introspect import introspect
from .schema import EXPECTED_TABLES
from .writer import ProjectSpec


class ValidationError(Exception):
    pass


def default_validate(conn: sqlite3.Connection, spec: ProjectSpec) -> None:
    # 1. Schema shape: the canonical Target Scheduler tables must all be present.
    tables = {name.lower() for name in introspect(conn)}
    missing = {t.lower() for t in EXPECTED_TABLES} - tables
    if missing:
        raise ValidationError(f"target DB missing expected tables: {sorted(missing)}")

    # 2. Profile id must be present (it scopes every row Target Scheduler reads).
    if not (spec.profile_id and spec.profile_id.strip()):
        raise ValidationError("profile_id is required")

    # 3. FK integrity of what we just inserted (authoritative cross-check; the
    #    orchestrator also runs PRAGMA foreign_key_check).
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValidationError(f"foreign key violations: {violations[:5]}")
