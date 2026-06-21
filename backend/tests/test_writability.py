"""Tests for unwritable-database reporting (dxg).

Writes go to a local copy and are published back into the database's folder, so the
health probe checks whether that folder accepts a create + rename — not whether the
source accepts in-place SQLite writes (which fails on a network share even though saves
succeed via staging). When the folder genuinely isn't writable, we surface a clear,
actionable message instead of a bare 'attempt to write a readonly database'.
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
def test_write_probe_passes_with_readonly_file_but_writable_dir(tmp_path):
    # A read-only DB *file* is fine: publish replaces it by renaming a new file into the
    # (writable) directory, so saves still work. The probe must NOT warn here.
    db = _make_db(tmp_path / "scheduler.sqlite")
    os.chmod(db, 0o444)
    try:
        assert _db_write_error(db) is None
    finally:
        os.chmod(db, 0o644)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission checks")
def test_write_probe_detects_unwritable_dir(tmp_path):
    db = _make_db(tmp_path / "scheduler.sqlite")
    os.chmod(tmp_path, 0o555)  # directory not writable -> can't publish
    try:
        msg = _db_write_error(db)
        assert msg is not None
        assert "writable" in msg.lower()
    finally:
        os.chmod(tmp_path, 0o755)  # let pytest clean up the tmp dir


def test_write_probe_none_when_no_db():
    assert _db_write_error(None) is None
