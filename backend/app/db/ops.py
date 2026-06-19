"""App-side write bookkeeping (data/ts_assistant_ops.json).

This deliberately holds ONLY the slice of write-path state that cannot live in
the Target Scheduler DB itself:

* **Per-target backup-coalescing state** — ``last_backup_time`` + ``last_seen_signature``
  per target DB, so our own back-to-back writes within the window share one backup
  while an external change forces a fresh one.
* **A lightweight operation log** — one entry per export/undo for a possible future
  restore/history UI. This is NOT a copy of row-level provenance: the authoritative
  record of *which rows are ours* lives in the in-DB ``ts_assistant_provenance`` table
  (see :mod:`app.db.provenance`).

Persistence mirrors :mod:`app.equipment` (read-modify-write a small JSON file).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ..config import DATA_DIR, ensure_dirs

OPS_FILE = DATA_DIR / "ts_assistant_ops.json"


class OpLogEntry(BaseModel):
    operation_id: str
    written_at: str  # ISO-8601 UTC
    kind: str = "export"  # export | undo
    target_db: str
    backup_path: str | None = None
    status: str  # committed | rolled_back | undone
    project_guid: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)


class TargetState(BaseModel):
    last_backup_time: str | None = None  # ISO-8601 UTC
    last_seen_signature: str | None = None


class OpsStore(BaseModel):
    version: int = 1
    targets: dict[str, TargetState] = Field(default_factory=dict)  # abs path -> state
    operations: list[OpLogEntry] = Field(default_factory=list)


def _read() -> OpsStore:
    ensure_dirs()
    if not OPS_FILE.is_file():
        return OpsStore()
    return OpsStore(**json.loads(OPS_FILE.read_text() or "{}"))


def _write(store: OpsStore) -> None:
    ensure_dirs()
    OPS_FILE.write_text(store.model_dump_json(indent=2))


# --- backup-coalescing state ----------------------------------------------

def get_target_state(target: str) -> TargetState:
    return _read().targets.get(target, TargetState())


def set_target_state(
    target: str,
    *,
    last_backup_time: str | None = None,
    last_seen_signature: str | None = None,
) -> None:
    """Patch a target's coalescing state (only the provided fields)."""
    store = _read()
    state = store.targets.get(target, TargetState())
    if last_backup_time is not None:
        state.last_backup_time = last_backup_time
    if last_seen_signature is not None:
        state.last_seen_signature = last_seen_signature
    store.targets[target] = state
    _write(store)


# --- operation log --------------------------------------------------------

def record_op(entry: OpLogEntry) -> None:
    store = _read()
    store.operations.append(entry)
    _write(store)


def find_op(operation_id: str) -> OpLogEntry | None:
    for op in _read().operations:
        if op.operation_id == operation_id:
            return op
    return None


def mark_status(operation_id: str, status: str) -> None:
    store = _read()
    for op in store.operations:
        if op.operation_id == operation_id:
            op.status = status
    _write(store)
