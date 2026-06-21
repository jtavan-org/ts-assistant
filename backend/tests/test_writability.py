"""Tests for read-only/unwritable database reporting (dxg).

A SQLite database can be readable but not writable — classically when it lives on a
network share or file-sync replica (SMB/CIFS/NFS). We translate that into a clear,
actionable error and a health-probe signal instead of a bare 'attempt to write a
readonly database'.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from app.db.export import (
    DatabaseBusyError,
    DatabaseReadOnlyError,
    ExportError,
    _busy_or_export_error,
)
from app.main import _db_write_error


def test_busy_or_export_error_maps_readonly():
    err = _busy_or_export_error(sqlite3.OperationalError("attempt to write a readonly database"))
    assert isinstance(err, DatabaseReadOnlyError)
    assert "read-only" in str(err).lower()
    assert "local storage" in str(err).lower()


def test_busy_or_export_error_maps_busy():
    err = _busy_or_export_error(sqlite3.OperationalError("database is locked"))
    assert isinstance(err, DatabaseBusyError)


def test_busy_or_export_error_passthrough():
    err = _busy_or_export_error(sqlite3.OperationalError("no such table: foo"))
    assert isinstance(err, ExportError)
    assert not isinstance(err, (DatabaseBusyError, DatabaseReadOnlyError))


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    return path


def test_write_probe_passes_on_writable_db(tmp_path):
    db = _make_db(tmp_path / "scheduler.sqlite")
    assert _db_write_error(db) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission checks")
def test_write_probe_detects_readonly_file(tmp_path):
    db = _make_db(tmp_path / "scheduler.sqlite")
    os.chmod(db, 0o444)
    try:
        msg = _db_write_error(db)
        assert msg is not None
        assert "read-only" in msg.lower()
    finally:
        os.chmod(db, 0o644)  # let pytest clean up the tmp dir


def test_write_probe_none_when_no_db():
    assert _db_write_error(None) is None
