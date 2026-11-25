"""Autogenerate comparison hooks shared by Alembic's env.py and the drift test."""

from __future__ import annotations

from typing import Any

from geoalchemy2 import Geography, Geometry


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    # Ignore reflected tables that are not ours (e.g. PostGIS `spatial_ref_sys`).
    return not (type_ == "table" and reflected and compare_to is None)


def compare_type(
    context: Any,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    # Spatial types reflect with slightly different parameters; compare by class only.
    if isinstance(metadata_type, Geography | Geometry):
        return not isinstance(inspected_type, Geography | Geometry)
    return None  # fall back to Alembic's default comparison
