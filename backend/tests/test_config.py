"""Tests for source-database resolution (app.config.find_source_db)."""

from __future__ import annotations

from app import config


def _touch(path):
    path.write_bytes(b"")
    return path


def test_prefers_canonical_over_backup(tmp_path, monkeypatch):
    """A real Target Scheduler folder holds schedulerdb.sqlite plus NINA's dated
    backups; the canonical file must win even though '-' sorts before '.'."""
    monkeypatch.delenv("TS_ASSISTANT_DB", raising=False)
    monkeypatch.setattr(config, "SAMPLE_DB_DIR", tmp_path)
    _touch(tmp_path / "schedulerdb-2026-06-16-21-23-06-backup.sqlite")
    canonical = _touch(tmp_path / "schedulerdb.sqlite")

    assert config.find_source_db() == canonical


def test_skips_backups_in_glob_fallback(tmp_path, monkeypatch):
    """With no canonical name, fall back to the first non-backup SQLite file."""
    monkeypatch.delenv("TS_ASSISTANT_DB", raising=False)
    monkeypatch.setattr(config, "SAMPLE_DB_DIR", tmp_path)
    _touch(tmp_path / "aaa-backup.sqlite")
    real = _touch(tmp_path / "my-scheduler.sqlite")

    assert config.find_source_db() == real


def test_returns_none_when_only_backups(tmp_path, monkeypatch):
    monkeypatch.delenv("TS_ASSISTANT_DB", raising=False)
    monkeypatch.setattr(config, "SAMPLE_DB_DIR", tmp_path)
    _touch(tmp_path / "schedulerdb-2026-01-01-00-00-00-backup.sqlite")

    assert config.find_source_db() is None


def test_explicit_env_path_takes_priority(tmp_path, monkeypatch):
    explicit = _touch(tmp_path / "elsewhere.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(explicit))
    monkeypatch.setattr(config, "SAMPLE_DB_DIR", tmp_path)
    _touch(tmp_path / "schedulerdb.sqlite")

    assert config.find_source_db() == explicit
