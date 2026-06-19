"""Export API — create a Target Scheduler project from staged edits (bead mh3.4).

Thin HTTP layer over the additive-only writer (:mod:`app.db.export`). The request
body is a :class:`ProjectSpec` of already-expanded targets (the frontend expands an
N×M mosaic into per-pane target centers with its tested ``mosaicPanels`` and posts
those, so the written geometry is exactly what the user previewed). Writes go to the
safe staging copy (``data/export/``); live writes remain env-gated in the writer and
are intentionally not exposed here.

Errors map to status codes the frontend can act on:
* 422 — the staged edits / target schema are invalid (``ValidationError``).
* 409 — the target DB is busy (NINA open) or the project has captured progress.
* 400 — any other export failure.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db.export import (
    DatabaseBusyError,
    ExportError,
    ExportResult,
    ProgressError,
    UndoResult,
    export_project,
    undo_operation,
)
from ..db.validate import ValidationError
from ..db.writer import ProjectSpec

router = APIRouter()


@router.post("/export", response_model=ExportResult)
def create_export(spec: ProjectSpec) -> ExportResult:
    """Back up, then additively write a project + its targets + exposure plans."""
    try:
        return export_project(spec)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (DatabaseBusyError, ProgressError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/export/{operation_id}/undo", response_model=UndoResult)
def undo_export(operation_id: str) -> UndoResult:
    """Remove exactly the rows a previous export added (refused if it has progress)."""
    try:
        return undo_operation(operation_id)
    except ProgressError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
