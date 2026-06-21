"""Tests for the DB change-token endpoint (kfc).

The frontend polls a cheap change token (source-DB size + mtime, reusing
``backup_signature``) and triggers a state-preserving UI refresh when it changes.
This covers both the dedicated ``/api/db-version`` endpoint and the ``db_version``
field added to ``/api/health``.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db.backup import backup_signature
from app.main import app


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def client_with_db(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "scheduler.sqlite")
    # main.find_source_db is imported into the module namespace, so patch it there.
    monkeypatch.setattr(main, "find_source_db", lambda: db)
    return TestClient(app), db


def test_db_version_matches_signature(client_with_db):
    client, db = client_with_db
    res = client.get("/api/db-version")
    assert res.status_code == 200
    assert res.json()["db_version"] == backup_signature(db)


def test_db_version_changes_on_write(client_with_db):
    client, db = client_with_db
    before = client.get("/api/db-version").json()["db_version"]
    assert before is not None

    # An external write (a new row) changes size and/or mtime -> a new token.
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO t (x) VALUES (1)")
    conn.commit()
    conn.close()

    after = client.get("/api/db-version").json()["db_version"]
    assert after is not None
    assert after != before


def test_health_includes_db_version(client_with_db):
    client, db = client_with_db
    body = client.get("/api/health").json()
    assert body["db_version"] == backup_signature(db)


def test_db_version_none_without_db(monkeypatch):
    monkeypatch.setattr(main, "find_source_db", lambda: None)
    client = TestClient(app)
    assert client.get("/api/db-version").json()["db_version"] is None
    assert client.get("/api/health").json()["db_version"] is None
