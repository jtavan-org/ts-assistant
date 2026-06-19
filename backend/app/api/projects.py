"""Read-only project/target endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..db.models import Project, SchemaInfo, Target
from ..db.reader import load_projects, load_targets, schema_info

router = APIRouter()


@router.get("/projects", response_model=list[Project])
def get_projects() -> list[Project]:
    return load_projects()


@router.get("/targets", response_model=list[Target])
def get_targets() -> list[Target]:
    return load_targets()


@router.get("/schema", response_model=SchemaInfo)
def get_schema() -> SchemaInfo:
    return schema_info()
